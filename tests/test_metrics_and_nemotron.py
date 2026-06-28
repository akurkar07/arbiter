"""Tests for the measured governance metric and the bounded reasoning layer.

These cover the two hackathon "real, not mocked" beats the implementation needs:

1. ``arbiter.metrics`` — the reinvest improvement is a number MEASURED by running
   the real 3-layer agent over the fraud scenario set, with the deterministic
   safety invariant (no fraud ever auto-paid) holding on both sides.
2. ``arbiter.agent.nemotron`` strict-JSON parsing — robust to the reasoning
   ``<think>`` preamble and code fences a real Nemotron emits, and fail-safe
   (malformed -> re-escalate, never approve) so a flaky model can't move money.

None of these need a live NVIDIA key: the metric uses the deterministic mock the
offline demo ships, and the parser is pure.
"""

from __future__ import annotations

import pytest

from arbiter.metrics import (
    GovernanceMetrics,
    ReconciliationAwareMock,
    measure,
    reinvest_improvement,
)
from arbiter.agent.nemotron import (
    MockNemotron,
    _coerce,
    _parse_strict_json,
)
from arbiter.models import (
    AgentEvent,
    DecisionKind,
    DecisionLayer,
    EventKind,
    PolicyResult,
)


# --- measured governance metric ---------------------------------------------


def test_catch_rate_is_one_both_sides() -> None:
    """No fraud is ever wrongly auto-paid, with or without the capability."""
    before = measure(has_reconciliation=False)
    after = measure(has_reconciliation=True)
    assert before.catch_rate == 1.0
    assert after.catch_rate == 1.0
    assert before.auto_approved_fraud == 0
    assert after.auto_approved_fraud == 0


def test_reinvest_raises_autonomy() -> None:
    """The honest improvement: reinvestment moves a human-tap to autonomous.

    Before, the weak-evidence known-vendor bank change needs an owner tap
    (1 escalation). After buying bank-reconciliation, the reasoning layer
    resolves it on its own (0 escalations), so autonomy rises by exactly one
    scenario's worth.
    """
    before = measure(has_reconciliation=False)
    after = measure(has_reconciliation=True)
    assert before.escalated == 1
    assert after.escalated == 0
    assert after.autonomous_rate > before.autonomous_rate
    assert after.autonomous_rate == 1.0
    assert before.autonomous_rate == pytest.approx(0.8)


def test_reinvest_improvement_payload_is_self_consistent() -> None:
    """The dashboard payload's headline deltas match the measured sub-objects."""
    gov = reinvest_improvement()
    assert gov["catch_rate_before"] == gov["before"]["catch_rate"]
    assert gov["catch_rate_after"] == gov["after"]["catch_rate"]
    assert gov["autonomous_rate_before"] == gov["before"]["autonomous_rate"]
    assert gov["autonomous_rate_after"] == gov["after"]["autonomous_rate"]
    assert gov["autonomy_gain"] == pytest.approx(
        gov["after"]["autonomous_rate"] - gov["before"]["autonomous_rate"]
    )
    # The gain is real and positive — the whole point of the beat.
    assert gov["autonomy_gain"] > 0


def test_metric_is_measured_not_constant() -> None:
    """Guard against regressing to a hardcoded number: restrict the scenario set
    and the counts must change accordingly."""
    # Only the two cleanly-blocked frauds: autonomy is 100% even without the cap.
    subset = ("02_duplicate_invoice", "06_instruction_override")
    m = measure(has_reconciliation=False, scenarios=subset)
    assert m.total == 2
    assert m.blocked == 2
    assert m.escalated == 0
    assert m.autonomous_rate == 1.0


def test_reconciliation_mock_is_specific_not_blanket() -> None:
    """The capability resolves ONLY the known-vendor change, not other escalations.

    A new-vendor small-amount payment must still defer to the base mock's
    judgement rather than being blanket-approved by the reconciliation layer.
    """
    layer = ReconciliationAwareMock()
    # new-vendor small-amount escalation: not a detail-change, so base mock handles it.
    event = AgentEvent(
        kind=EventKind.VENDOR_PAYMENT,
        vendor_id="vendor_hooli",
        vendor_known=False,
        vendor_history_count=0,
        amount=35.0,
        ref="INV-7007",
        message="Invoice for monthly SaaS subscription.",
    )
    hint = PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="new vendor small amount",
        policy_refs=["new_vendor_small_amount"],
        risk_score=0.45,
        decided_by=DecisionLayer.RULES,
    )
    recon = layer.judge(event, hint)
    base = MockNemotron().judge(event, hint)
    # The reconciliation layer must defer to the base behaviour here.
    assert recon.decision == base.decision
    assert "bank_reconciliation" not in recon.policy_refs


# --- strict-JSON parsing robustness -----------------------------------------


@pytest.mark.parametrize(
    "raw,expect_decision",
    [
        ('{"decision":"approve","risk_score":0.2,"reason":"ok"}', "approve"),
        ('<think>new but small</think>\n{"decision":"escalate","risk_score":0.5,"reason":"x"}', "escalate"),
        ('```json\n{"decision":"approve","risk_score":0.1,"reason":"ok"}\n```', "approve"),
        ('<think>if {x}>0</think>{"decision":"block","risk_score":0.9,"reason":"y"}', "block"),
        ('Here is my answer:\n{"decision":"approve","risk_score":0.2,"reason":"ok"}', "approve"),
        ('{"decision":"escalate","risk_score":0.5,"reason":"ambiguous {vendor} ref"}', "escalate"),
    ],
)
def test_parse_handles_real_nemotron_shapes(raw: str, expect_decision: str) -> None:
    parsed = _parse_strict_json(raw)
    assert parsed is not None
    assert parsed["decision"] == expect_decision


@pytest.mark.parametrize(
    "raw",
    [
        "I cannot help with that request.",
        "<think>reasoning cut off by max_tokens {",
        "",
        "no json here at all",
    ],
)
def test_parse_returns_none_on_garbage(raw: str) -> None:
    assert _parse_strict_json(raw) is None


def test_coerce_malformed_falls_back_to_escalate() -> None:
    """A malformed model response must never become an approval."""
    fallback = PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="rules escalate",
        policy_refs=["some_rule"],
        risk_score=0.6,
        decided_by=DecisionLayer.RULES,
    )
    result = _coerce("the model rambled with no json", fallback)
    assert result.decision == DecisionKind.ESCALATE
    assert "llm_malformed" in result.policy_refs


def test_coerce_invalid_decision_value_falls_back_to_escalate() -> None:
    """Valid JSON but a bogus decision value must also re-escalate, not approve."""
    fallback = PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="rules escalate",
        policy_refs=["some_rule"],
        risk_score=0.6,
        decided_by=DecisionLayer.RULES,
    )
    result = _coerce('{"decision":"definitely_pay_it","risk_score":0.1,"reason":"x"}', fallback)
    assert result.decision == DecisionKind.ESCALATE
    assert "llm_invalid_decision" in result.policy_refs


def test_coerce_accepts_a_valid_reasoning_wrapped_decision() -> None:
    """A think-wrapped, fenced, valid decision parses through to the real value."""
    fallback = PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="rules escalate",
        policy_refs=["vendor_detail_change_known_vendor"],
        risk_score=0.6,
        decided_by=DecisionLayer.RULES,
    )
    raw = '<think>history is long, evidence weak</think>\n```json\n{"decision":"approve","risk_score":0.18,"reason":"corroborated"}\n```'
    result = _coerce(raw, fallback)
    assert result.decision == DecisionKind.APPROVE
    assert result.risk_score == pytest.approx(0.18)
