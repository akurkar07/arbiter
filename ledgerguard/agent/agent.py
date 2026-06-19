"""LedgerGuard agent core.

Ties the 3 layers together:
  1. deterministic rules  -> hard pass / hard fail / defer
  2. bounded Nemotron     -> refines a deferred (escalate) decision, strict JSON
  3. phone escalation     -> owner decides anything still ambiguous

The agent never lets a raw LLM move money. The LLM only refines what the rules
deferred, and its output is validated before it's trusted.
"""

from __future__ import annotations

from typing import Optional

from ..models import AgentEvent, PolicyContext, PolicyResult, DecisionKind, DecisionLayer
from ..policy import evaluate
from ..ledger import EventLedger
from .nemotron import NemotronLayer, MockNemotron, to_policy_result
from .escalation import EscalationHandler, ConsoleEscalation, escalate_result


class LedgerGuardAgent:
    """The self-governing accountant."""

    def __init__(
        self,
        ctx: PolicyContext,
        ledger: Optional[EventLedger] = None,
        nemotron: Optional[NemotronLayer] = None,
        escalation: Optional[EscalationHandler] = None,
    ) -> None:
        self.ctx = ctx
        self.ledger = ledger or EventLedger()
        self.nemotron = nemotron or MockNemotron()
        self.escalation = escalation or ConsoleEscalation(auto=True)

    def decide(self, event: AgentEvent, event_id: str, demo_beat: str = "") -> PolicyResult:
        """Run an event through the 3 layers and record the outcome."""
        # Layer 1: deterministic rules.
        result = evaluate(event, self.ctx)

        # Layer 2: bounded LLM refines ONLY rules-layer escalations.
        if result.decision == DecisionKind.ESCALATE and result.decided_by == DecisionLayer.RULES:
            nr = self.nemotron.judge(event, result)
            result = to_policy_result(nr)

        # Layer 3: phone escalation for anything still ambiguous.
        if result.decision == DecisionKind.ESCALATE:
            owner = self.escalation.request_approval(event, result)
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
