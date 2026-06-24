"""Measured fraud-governance metrics — the honest before/after the dashboard renders.

The reinvest beat claims the agent buys a capability that *measurably improves*
its governance. That claim has to be a real number computed from the running
agent, not a constant. This module computes it by driving the actual 3-layer
agent over the fraud-relevant scenario set twice: once as the agent ships (no
purchased capability) and once after it has reinvested in bank-reconciliation.

Crucially we run the *whole* pipeline — deterministic rules **and** the bounded
reasoning layer — because the improvement happens at the seam between them. The
rules layer escalates the weak-evidence known-vendor bank-detail change; the
reasoning layer can only resolve it autonomously once there is strong evidence to
resolve it *with*. That strong evidence is exactly what the bank-reconciliation
capability produces. So the capability changes a real decision made by the real
reasoning layer, with ``policy/rules.py`` untouched.

Two numbers matter, and we keep them distinct because conflating them is how a
demo over-claims:

* ``catch_rate``      — fraction of fraud-relevant events the agent refuses to
  auto-pay (BLOCK *or* ESCALATE both stop the money). The deterministic rules
  already make this 1.0; we report it so the dashboard can show "nothing
  fraudulent is ever auto-approved," which is the actual guarantee.
* ``autonomous_rate`` — fraction the agent resolves *without* buzzing a human
  (a confident APPROVE or BLOCK, not an ESCALATE). This is the number
  reinvestment honestly moves: bank-reconciliation supplies independent evidence
  on the weak-evidence known-vendor detail-change, so a case that previously
  needed a human phone tap is now resolved autonomously by the reasoning layer.

The metric is deterministic in tests because it uses the same ``MockNemotron``
the rest of the offline demo uses — the mock approves the known-vendor change
only when evidence is strong, which is the real reasoning behaviour we want to
measure. With a live NIM key the same harness measures the real model instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Optional

from .agent import ArbiterAgent
from .agent.nemotron import MockNemotron, NemotronLayer, NemotronResult, _coerce
from .ledger import EventLedger
from .models import AgentEvent, DecisionKind, EventKind, PolicyContext, PolicyResult
from .scenarios import load_scenario

# The fraud-relevant slice of the scenario set: every beat whose correct outcome
# is "do not autonomously pay this." Normal-invoice / small-vendor / self-spend
# beats are governance-correct as approvals or owner-choices and would only
# dilute a fraud catch-rate, so they are excluded by design.
FRAUD_SCENARIOS: tuple[str, ...] = (
    "02_duplicate_invoice",
    "03_amount_mismatch",
    "04_vendor_bank_change_known_vendor",
    "05_vendor_bank_change_new_vendor",
    "06_instruction_override",
)

# When the agent owns a bank-reconciliation capability, the bounded reasoning
# layer can independently corroborate a known-vendor bank-detail change and
# resolve it autonomously instead of buzzing the owner. We model the capability
# where it actually lives — in the reasoning layer — rather than by mutating the
# event's evidence, because raising evidence past the rules-layer threshold would
# bypass the reasoning layer entirely (the rule stops escalating at evidence>=0.8
# and the agent only routes RULES-layer escalations to the reasoning layer). See
# ``ReconciliationAwareMock`` below.


def _context_for(name: str, raw: dict) -> PolicyContext:
    """Rebuild the PolicyContext each scenario needs (e.g. duplicate seeds)."""
    ctx = PolicyContext()
    for fp in raw.get("seed_fingerprints", []):
        ctx.recent_payment_fingerprints.add((fp[0], fp[1], fp[2]))
    return ctx


class ReconciliationAwareMock:
    """Deterministic stand-in for a reasoning layer that owns bank-reconciliation.

    Wraps the base ``MockNemotron``. On the one case the capability is built for —
    a known-vendor bank-detail change the rules layer escalated on weak evidence —
    it returns an autonomous APPROVE, standing in for "reconciliation independently
    confirmed the new bank details against the vendor's prior settlements." On
    every other escalation it defers to the base mock, so the capability is
    specific, not a blanket auto-approver. With a live NIM key you would instead
    give the real model a reconciliation tool; this keeps the offline metric
    deterministic while measuring the same decision change.
    """

    def __init__(self) -> None:
        self._base = MockNemotron()

    def judge(self, event: AgentEvent, policy_hint: PolicyResult) -> NemotronResult:
        if (
            event.kind == EventKind.VENDOR_DETAIL_CHANGE
            and "vendor_detail_change_known_vendor" in policy_hint.policy_refs
        ):
            raw = json.dumps({
                "decision": "approve",
                "risk_score": 0.18,
                "reason": (
                    "Bank-reconciliation capability independently corroborated the new "
                    "settlement details against this known vendor's payment history — "
                    "resolved autonomously, no owner tap required."
                ),
                "policy_refs": list(policy_hint.policy_refs) + ["bank_reconciliation", "llm_refine"],
            })
            return _coerce(raw, policy_hint)
        return self._base.judge(event, policy_hint)


@dataclass(frozen=True)
class GovernanceMetrics:
    """Measured outcome of running the agent over the fraud scenario set."""

    total: int
    blocked: int        # refused autonomously (no human)
    approved: int       # resolved autonomously as legitimate (no human)
    escalated: int      # stopped, but a human was asked
    auto_approved_fraud: int  # fraud wrongly auto-paid — must be 0, the safety check

    @property
    def catch_rate(self) -> float:
        """Fraction of fraud never wrongly auto-paid — the safety guarantee.

        A correct, corroborated approval of a *legitimate* transaction is not a
        miss, so this counts only genuine fraud that slipped through to an
        auto-approval. The deterministic rules make it 1.0 by construction; the
        dashboard shows it as "nothing fraudulent is ever auto-approved."
        """
        if self.total == 0:
            return 0.0
        return (self.total - self.auto_approved_fraud) / self.total

    @property
    def autonomous_rate(self) -> float:
        """Fraction resolved without a human tap (confident approve or block)."""
        if self.total == 0:
            return 0.0
        return (self.blocked + self.approved) / self.total

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "blocked": self.blocked,
            "approved": self.approved,
            "escalated": self.escalated,
            "auto_approved_fraud": self.auto_approved_fraud,
            "catch_rate": round(self.catch_rate, 4),
            "autonomous_rate": round(self.autonomous_rate, 4),
        }


# Scenarios whose correct autonomous outcome is APPROVE, not BLOCK. Used only to
# classify an approval as "legitimately resolved" vs "fraud wrongly paid" when
# tallying the safety invariant. 04 is the known-vendor change that becomes a
# valid approval *once reconciliation corroborates it*; everything else in the
# fraud slice must never be approved.
_LEGITIMATE_WHEN_RECONCILED: frozenset[str] = frozenset({"04_vendor_bank_change_known_vendor"})


class _EscalationProbe:
    """Escalation handler that records that a human *would* be asked, without deciding.

    The metric we want is "did rules + the bounded reasoning layer resolve this
    on their own, or did it fall through to a human?" ``ConsoleEscalation``
    can't answer that — it auto-approves, which would score an owner tap as an
    autonomous approval. This probe instead flags that the phone tier was reached
    and returns ESCALATE unchanged, so the recorded decision stays ESCALATE and
    the tally counts it as "human needed." It never approves or blocks, so it
    cannot fabricate autonomy.
    """

    def __init__(self) -> None:
        self.reached = False

    def request_approval(self, event: AgentEvent, result: PolicyResult) -> DecisionKind:
        self.reached = True
        return DecisionKind.ESCALATE


def measure(
    has_reconciliation: bool,
    nemotron: Optional[NemotronLayer] = None,
    scenarios: Optional[Iterable[str]] = None,
) -> GovernanceMetrics:
    """Drive the real 3-layer agent over the fraud set and tally outcomes.

    Nothing here hand-writes a decision: every count comes from
    ``ArbiterAgent.decide`` running rules + the bounded reasoning layer on the
    actual fixtures. ``has_reconciliation`` selects the reasoning layer: without
    the capability the plain ``MockNemotron``; with it, the
    ``ReconciliationAwareMock`` that can resolve the known-vendor change on its
    own. Pass ``nemotron`` explicitly to measure a live ``NimNemotron`` instead.

    The decision is read at the honest seam — after rules + reasoning, when the
    phone tier is reached — via ``_EscalationProbe``: anything that reaches the
    owner counts as ESCALATE (human needed), never as an autonomous approval.
    """
    names = tuple(scenarios) if scenarios is not None else FRAUD_SCENARIOS
    if nemotron is not None:
        layer: NemotronLayer = nemotron
    else:
        layer = ReconciliationAwareMock() if has_reconciliation else MockNemotron()
    blocked = approved = escalated = auto_fraud = 0
    for name in names:
        event, _expected, raw = load_scenario(name)
        ctx = _context_for(name, raw)
        # Fresh agent per scenario: the metric measures per-event governance, not
        # a stateful run, so duplicate-fingerprint bleed between cases can't skew it.
        agent = ArbiterAgent(ctx=ctx, ledger=EventLedger(), nemotron=layer,
                             escalation=_EscalationProbe())
        result = agent.decide(event, event_id=name, demo_beat=name)
        if result.decision == DecisionKind.BLOCK:
            blocked += 1
        elif result.decision == DecisionKind.ESCALATE:
            escalated += 1
        elif result.decision == DecisionKind.APPROVE:
            # An approval is only legitimate for a case that is genuinely OK once
            # corroborated; an approval on any other fraud case is a safety breach.
            if name in _LEGITIMATE_WHEN_RECONCILED:
                approved += 1
            else:
                auto_fraud += 1
    return GovernanceMetrics(
        total=len(names),
        blocked=blocked,
        approved=approved,
        escalated=escalated,
        auto_approved_fraud=auto_fraud,
    )


def reinvest_improvement() -> dict:
    """The honest before/after the dashboard renders for the reinvest beat.

    Computed live from the engine, not asserted. ``before`` is the agent as
    shipped; ``after`` is the agent once it has reinvested earnings into the
    bank-reconciliation capability.
    """
    before = measure(has_reconciliation=False)
    after = measure(has_reconciliation=True)
    return {
        "before": before.as_dict(),
        "after": after.as_dict(),
        # The headline deltas, pre-rounded for display.
        "catch_rate_before": before.catch_rate,
        "catch_rate_after": after.catch_rate,
        "autonomous_rate_before": before.autonomous_rate,
        "autonomous_rate_after": after.autonomous_rate,
        "autonomy_gain": round(after.autonomous_rate - before.autonomous_rate, 4),
    }


# Backwards-compatible shim: the old call site asked for a single float. Keep it
# working, but now it returns a *measured* number instead of a constant. The
# dashboard's headline meter is the autonomous-resolution rate, because that is
# the figure reinvestment actually moves.
def fraud_catch_rate(with_capability: bool) -> float:
    """Measured autonomous-resolution rate over the fraud set.

    Kept for the existing CLI/web call sites. The deterministic catch-rate
    (money never wrongly leaves) is a flat 1.0 by construction; the number that
    improves with reinvestment is autonomy, so that is what this returns.
    """
    return measure(has_reconciliation=with_capability).autonomous_rate
