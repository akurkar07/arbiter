"""Locks the business-day demo story + the ledger/escalation behaviour it relies on.

The business day IS the pitch. If any beat silently flips verdict, the video
lies. These tests pin the seven-beat outcome end-to-end through the real engine,
plus the two supporting behaviours added for it:
  * an approved supplier payment counts as spend (the AP-autopilot moved money);
  * a held escalation stays ESCALATE (the owner-tap beat is not auto-resolved).
"""

from __future__ import annotations

from arbiter.business_day import run, business_day_events
from arbiter.ledger import EventLedger
from arbiter.agent import ArbiterAgent
from arbiter.agent.escalation import HoldEscalation
from arbiter.agent.nemotron import MockNemotron
from arbiter.models import AgentEvent, EventKind, DecisionKind, PolicyContext


def test_business_day_story_is_stable():
    """The seven beats must resolve exactly as the narrative claims."""
    out = run(interactive=False)
    timeline = out["timeline"]
    decisions = {row["id"]: row["decision"] for row in timeline}

    assert decisions["01_revenue_in"] == "approve"       # earn
    assert decisions["02_pay_aws"] == "approve"          # autopilot pays
    assert decisions["03_pay_acme"] == "approve"         # autopilot pays
    assert decisions["04_aws_duplicate"] == "block"      # double-pay caught
    assert decisions["05_northstar_overpay"] == "block"  # overpay caught
    assert decisions["06_unapproved_payee"] == "block"   # the stranger — allowlist
    assert decisions["07_northstar_bank_change"] == "escalate"  # owner tap


def test_business_day_economics():
    """Real P&L: £480 in, £360 out to two suppliers, +£120 net, 3 blocks, 1 ask."""
    out = run(interactive=False)
    assert out["earnings"] == 480.0
    assert out["spend"] == 360.0          # AWS 220 + Acme 140
    assert out["net"] == 120.0
    assert out["tally"] == {"approve": 3, "block": 3, "escalate": 1}


def test_unapproved_payee_block_is_the_allowlist_rule():
    """Beat 6's block must come from the allowlist, not some incidental rule."""
    out = run(interactive=False)
    row = next(r for r in out["timeline"] if r["id"] == "06_unapproved_payee")
    assert "payee_not_approved" in row["refs"]


def test_approved_vendor_payment_counts_as_spend():
    """The ledger must count an approved supplier payment as money out."""
    led = EventLedger()
    agent = ArbiterAgent(
        ctx=PolicyContext(approved_payees={"aws"}),
        ledger=led,
        nemotron=MockNemotron(),
        escalation=HoldEscalation(),
    )
    agent.decide(
        AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="aws", amount=220.0,
                   invoice_amount=220.0, ref="AWS-1", vendor_known=True,
                   vendor_history_count=9),
        event_id="t_pay",
    )
    assert led.spend == 220.0
    assert led.net == -220.0


def test_blocked_payment_never_counts_as_spend():
    """A blocked off-list payment must not move the spend total."""
    led = EventLedger()
    agent = ArbiterAgent(
        ctx=PolicyContext(approved_payees={"aws"}),
        ledger=led,
        nemotron=MockNemotron(),
        escalation=HoldEscalation(),
    )
    agent.decide(
        AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="stranger", amount=999.0,
                   invoice_amount=999.0, ref="X-1", vendor_known=True,
                   vendor_history_count=9),
        event_id="t_block",
    )
    assert led.spend == 0.0


def test_hold_escalation_keeps_beat_pending():
    """HoldEscalation must leave the decision as a clean ESCALATE, unstamped."""
    led = EventLedger()
    agent = ArbiterAgent(
        ctx=PolicyContext(approved_payees={"northstar_studio"}),
        ledger=led,
        nemotron=MockNemotron(),
        escalation=HoldEscalation(),
    )
    result = agent.decide(
        AgentEvent(kind=EventKind.VENDOR_DETAIL_CHANGE, vendor_id="northstar_studio",
                   ref="NS-1", vendor_known=True, vendor_history_count=5,
                   detail_change_evidence=0.3,
                   message="we changed banks, pay the new account"),
        event_id="t_hold",
    )
    assert result.decision is DecisionKind.ESCALATE
    assert "[owner decision]" not in result.reason  # not phantom-resolved


def test_business_day_has_seven_beats():
    assert len(business_day_events()) == 7
