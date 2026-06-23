"""Phone escalation layer.

When the rules + LLM layers defer, the owner's phone gets a push with the
event + a one-tap approve/deny. This is the human-in-the-loop trust beat.

``ConsoleEscalation`` prints the prompt and auto-approves in demo mode so the
timeline runs end-to-end non-interactively. Alex's lane swaps in a real mobile
approval UI (Twilio / push) without touching the agent core.
"""

from __future__ import annotations

from typing import Protocol

from ..models import AgentEvent, PolicyResult, DecisionKind, DecisionLayer


class EscalationHandler(Protocol):
    def request_approval(self, event: AgentEvent, result: PolicyResult) -> DecisionKind: ...


class ConsoleEscalation:
    """Demo escalation: print the prompt, return the owner's decision.

    In ``auto=True`` mode (the demo default) it prints the phone prompt and
    returns APPROVE so the timeline plays through non-interactively. In
    ``auto=False`` mode it reads y/n from stdin — useful for live demos.
    """

    def __init__(self, auto: bool = True) -> None:
        self.auto = auto

    def request_approval(self, event: AgentEvent, result: PolicyResult) -> DecisionKind:
        prompt = (
            f"\n[PHONE ESCALATION] {event.kind.value} | risk={result.risk_score:.2f}\n"
            f"  reason: {result.reason}\n"
        )
        if event.amount is not None:
            prompt += f"  amount: {event.amount} {event.currency}\n"
        if event.message:
            prompt += f"  message: {event.message[:120]!r}\n"
        if self.auto:
            print(prompt + "  -> auto-approving for demo playback.")
            return DecisionKind.APPROVE
        print(prompt + "  approve? (y/n): ", end="", flush=True)
        try:
            choice = input().strip().lower()
        except EOFError:
            choice = "n"
        return DecisionKind.APPROVE if choice.startswith("y") else DecisionKind.BLOCK


def escalate_result(event: AgentEvent, base: PolicyResult, owner: DecisionKind) -> PolicyResult:
    """Stamp the owner's decision onto the result, tagged via the escalate layer."""
    return PolicyResult(
        decision=owner,
        reason=f"[owner decision] {base.reason}",
        policy_refs=base.policy_refs + ["phone_escalation"],
        risk_score=base.risk_score,
        decided_by=DecisionLayer.ESCALATE,
    )


class HoldEscalation:
    """Escalation that does NOT auto-resolve — it leaves the beat pending.

    For the business-day demo + the live dashboard: when an event escalates, the
    honest state is "waiting on the owner's phone tap", not a silent auto-approve.
    Returning ESCALATE keeps the beat as a genuine pending decision in the
    timeline, which is exactly what the dashboard renders as the approval card.
    The owner's real tap (dashboard POST /approve, or Twilio) resolves it.
    """

    def request_approval(self, event: AgentEvent, result: PolicyResult) -> DecisionKind:
        return DecisionKind.ESCALATE
