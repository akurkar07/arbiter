"""Arbiter agent core.

Ties the 3 layers together:
  1. deterministic rules  -> hard pass / hard fail / defer
  2. bounded Nemotron     -> refines a deferred (escalate) decision, strict JSON
  3. phone escalation     -> owner decides anything still ambiguous

The agent never lets a raw LLM move money. The LLM only refines what the rules
deferred, and its output is validated before it's trusted.

Single money door — why ``settle`` exists alongside ``decide``:
  ``decide`` is pure judgment: it runs the 3 layers and records the verdict, but
  moves no money. ``settle`` is the ONLY path that touches the Stripe rail — it
  decides AND, on approve, executes the payment itself, returning the rail
  receipt. The agent holds the Stripe key; no caller is handed a way to pay
  around the engine. That inversion is what turns "the agent is told to call
  the governance layer" into "the agent cannot move money any other way":
  skipping the gate doesn't skip a check, it skips the only door to the money.
"""

from __future__ import annotations

from typing import Optional

from ..models import (
    AgentEvent,
    PolicyContext,
    PolicyResult,
    SettlementResult,
    DecisionKind,
    DecisionLayer,
    EventKind,
)
from ..policy import evaluate
from ..ledger import EventLedger
from ..stripe_glue import StripeGlue
from .nemotron import NemotronLayer, MockNemotron, to_policy_result
from .escalation import EscalationHandler, ConsoleEscalation, escalate_result


class ArbiterAgent:
    """The self-governing accountant.

    Holds the Stripe rail. Money only moves through :meth:`settle`, which fuses
    the governance decision with execution so the two can't be pried apart.
    """

    def __init__(
        self,
        ctx: PolicyContext,
        ledger: Optional[EventLedger] = None,
        nemotron: Optional[NemotronLayer] = None,
        escalation: Optional[EscalationHandler] = None,
        stripe: Optional[StripeGlue] = None,
    ) -> None:
        self.ctx = ctx
        self.ledger = ledger or EventLedger()
        self.nemotron = nemotron or MockNemotron()
        self.escalation = escalation or ConsoleEscalation(auto=True)
        # The agent is the sole key-holder. The default recording stub moves no
        # real money; select_stripe() swaps in the live test-mode rail when a key
        # is present. No other object in the system gets a Stripe handle.
        self.stripe = stripe or StripeGlue()

    def decide(self, event: AgentEvent, event_id: str, demo_beat: str = "") -> PolicyResult:
        """Run an event through the 3 layers and record the outcome — no money moves.

        This is pure judgment: the engine's verdict, recorded to the ledger. It
        is the primitive ``settle`` is built on, and the path tests/introspection
        use to read a decision without side effects. To actually pay, call
        ``settle`` — ``decide`` deliberately cannot reach the rail.
        """
        # Layer 1: deterministic rules.
        result = evaluate(event, self.ctx)

        # Layer 2: bounded LLM refines ONLY rules-layer escalations.
        if result.decision == DecisionKind.ESCALATE and result.decided_by == DecisionLayer.RULES:
            nr = self.nemotron.judge(event, result)
            result = to_policy_result(nr)

        # Layer 3: phone escalation for anything still ambiguous.
        if result.decision == DecisionKind.ESCALATE:
            owner = self.escalation.request_approval(event, result)
            # A handler that returns ESCALATE is *holding* for a real owner tap
            # (HoldEscalation) — keep the beat as a clean pending escalation
            # rather than stamping a phantom "[owner decision]" onto it.
            if owner != DecisionKind.ESCALATE:
                result = escalate_result(event, result, owner)

        # Record + bookkeeping.
        self.ledger.record(event, result, event_id, demo_beat)

        # If this was an approved self-spend, decrement the remaining budget.
        if event.kind.value == "self_spend" and result.decision == DecisionKind.APPROVE:
            self.ctx.budget_remaining -= event.amount or 0.0

        # If this was an approved invoice payment, seed the fingerprint so a
        # replayed copy is caught as a duplicate next time.
        if event.kind.value in ("invoice_payment", "vendor_payment") and result.decision == DecisionKind.APPROVE:
            if event.vendor_id and event.amount is not None and event.ref:
                self.ctx.recent_payment_fingerprints.add((event.vendor_id, event.amount, event.ref))

        return result

    def settle(self, event: AgentEvent, event_id: str, demo_beat: str = "") -> SettlementResult:
        """The single money door: decide, and on APPROVE execute on the rail.

        This is the only method that moves money. It runs ``decide`` for the
        governance verdict, then — and only if that verdict is APPROVE — performs
        the matching Stripe operation and returns the rail receipt. A block or an
        escalate returns with ``executed=False`` and ``stripe_id=None``: proof no
        money moved. Because the agent owns the Stripe handle, there is no way for
        a caller to reach APPROVE-then-pay without coming through here.
        """
        result = self.decide(event, event_id=event_id, demo_beat=demo_beat)

        executed = False
        stripe_id: Optional[str] = None

        if result.decision == DecisionKind.APPROVE:
            call = self._execute(event)
            # Tag the rail call with the governance event id so the dashboard can
            # join a paid spend row to its Stripe receipt. Additive: never alters
            # the decision or the money, only the provenance record.
            if call is not None:
                call.event_id = event_id
            # A rail call that was attempted but errored is recorded with
            # failed=True (the live glue records rather than raises so governance
            # never crashes). Such a call did NOT settle, so it must not be
            # reported as executed — that honesty is what reconciliation and a
            # judge rely on. A stub call (no real id, failed=False) still counts:
            # it is the demo's recorded money movement.
            if call is not None and not getattr(call, "failed", False):
                executed = True
                stripe_id = call.stripe_id

        return SettlementResult(
            decision=result.decision,
            reason=result.reason,
            policy_refs=list(result.policy_refs),
            risk_score=result.risk_score,
            decided_by=result.decided_by,
            executed=executed,
            stripe_id=stripe_id,
            stripe_backend=getattr(self.stripe, "backend", "stub"),
            event_id=event_id,
        )

    def _execute(self, event: AgentEvent):
        """Perform the Stripe operation for an APPROVED event. Internal only.

        Maps an event kind to its rail primitive. Returns the StripeCall (which
        carries the settlement id) or None for kinds that move no money on the
        out-rail (e.g. an inbound invoice payment is recorded as a checkout +
        webhook, not a payout).
        """
        if event.kind == EventKind.VENDOR_PAYMENT and event.vendor_id:
            return self.stripe.pay_supplier(
                event.vendor_id, event.amount or 0.0, event.currency, ref=event.ref
            )
        if event.kind == EventKind.SELF_SPEND and event.category:
            return self.stripe.provision_capability(
                event.category, event.amount or 0.0, event.currency
            )
        if event.kind == EventKind.INVOICE_PAYMENT:
            # Inbound money: record the checkout + its completion webhook. This
            # is the earn side; it produces a checkout id, not an outbound payout.
            self.stripe.create_checkout(event.ref or "n/a", event.amount or 0.0, event.currency)
            return self.stripe.webhook_received("checkout.session.completed", event.ref)
        return None
