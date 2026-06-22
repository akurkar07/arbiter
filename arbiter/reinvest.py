"""Self-funded reinvestment + self-guardrail.

When earnings cross a threshold, the agent proposes buying a capability that
improves its fraud catch-rate. The purchase is a SELF_SPEND event that goes
through the *same* policy engine as any other spend — so the agent's own
policy can block it if it's over budget or off-goal. This is the demo's
self-governance beat: it earns, reinvests, and blocks its own bad spending.

The honest improvement metric: a new fraud-detection capability raises the
catch-rate on the scenario set. We measure it for real — no fake numbers.
"""

from __future__ import annotations

from .models import AgentEvent, EventKind


# Earnings threshold before the agent proposes a reinvest.
REINVEST_THRESHOLD: float = 400.0


def maybe_reinvest_event(earnings: float, threshold: float = REINVEST_THRESHOLD) -> AgentEvent | None:
    """If earnings crossed the threshold, return the approved-capability spend event.

    The agent proposes the *allowed* purchase (fraud_detection, within budget).
    The over-budget and off-goal attempts are separate demo scenarios, not
    triggered here — they're in the fixture set to show the self-block beats.
    """
    if earnings < threshold:
        return None
    return AgentEvent(
        kind=EventKind.SELF_SPEND,
        amount=60.0,
        category="fraud_detection",
        message="Earnings threshold crossed — autonomously reinvesting in fraud-detection capability.",
    )


def fraud_catch_rate(with_capability: bool) -> float:
    """Honest improvement metric, MEASURED on the scenario set.

    Delegates to :mod:`arbiter.metrics`, which runs the real policy engine over
    the fraud-relevant fixtures and tallies the outcomes — no constants. The
    figure returned is the *autonomous-resolution rate* (fraud the agent refuses
    without asking a human), which is the number reinvestment actually moves:
    buying bank-reconciliation lets the agent resolve the weak-evidence
    known-vendor detail-change on its own instead of buzzing the owner.

    The deterministic catch-rate (fraud never auto-paid) is a flat 1.0 by
    construction; see ``metrics.reinvest_improvement`` for both figures and the
    full before/after the dashboard renders.
    """
    from .metrics import fraud_catch_rate as _measured

    return _measured(with_capability)
