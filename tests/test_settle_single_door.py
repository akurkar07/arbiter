"""The single money door: prove settle() is the only path to the rail.

These tests lock the inversion that turns Arbiter from "the agent is told to
call the governance layer" into "the agent cannot move money any other way":

  * decide() is pure judgment — it records a verdict but moves NO money, even on
    APPROVE. Nothing reaches Stripe.
  * settle() decides AND, on APPROVE only, executes on the rail the agent alone
    holds, returning the rail receipt.
  * a BLOCK or ESCALATE settles with executed=False and touches the rail zero
    times — a None/empty rail is proof no money moved.

Because the agent owns the Stripe handle and these are the only two entry points,
there is no APPROVE-then-pay path that lives outside settle().
"""

import pytest

from arbiter.agent import ArbiterAgent
from arbiter.models import AgentEvent, EventKind, DecisionKind
from arbiter.policy.config import demo_policy_context
from arbiter.stripe_glue import StripeGlue


@pytest.fixture
def agent():
    """A hermetic agent on the demo allowlist with a recording Stripe stub."""
    return ArbiterAgent(ctx=demo_policy_context(), stripe=StripeGlue())


def _pay(vendor_id, amount, ref, **kw):
    return AgentEvent(
        kind=EventKind.VENDOR_PAYMENT, vendor_id=vendor_id, amount=amount,
        invoice_amount=kw.get("invoice_amount", amount), currency="GBP",
        vendor_known=kw.get("vendor_known", True),
        vendor_history_count=kw.get("history", 11), ref=ref,
    )


def test_decide_moves_no_money_even_on_approve(agent):
    """decide() is pure judgment: an APPROVE records a verdict but pays nothing."""
    res = agent.decide(_pay("aws", 220.0, "aws-1"), event_id="d1")
    assert res.decision == DecisionKind.APPROVE
    # The rail was never touched — decide() physically cannot reach it.
    assert agent.stripe.calls == []


def test_settle_executes_only_on_approve(agent):
    """settle() pays an approved supplier and returns the rail receipt."""
    res = agent.settle(_pay("aws", 220.0, "aws-1"), event_id="s1")
    assert res.decision == DecisionKind.APPROVE
    assert res.executed is True
    # Exactly one supplier payment hit the agent's rail.
    pays = [c for c in agent.stripe.calls if c.op == "pay_supplier"]
    assert len(pays) == 1
    assert pays[0].payee == "aws" and pays[0].amount == 220.0


def test_settle_blocks_unapproved_payee_with_zero_rail_calls(agent):
    """An off-list payee is blocked AND no money moves — the core guarantee."""
    res = agent.settle(_pay("meta_ads", 300.0, "meta-1"), event_id="s2")
    assert res.decision == DecisionKind.BLOCK
    assert "payee_not_approved" in res.policy_refs
    # The settlement carries the proof: nothing executed, no rail handle.
    assert res.executed is False
    assert res.stripe_id is None
    assert res.moved_money is False
    # And the rail itself was never touched.
    assert agent.stripe.calls == []


def test_settle_blocks_overpay_with_zero_rail_calls(agent):
    """An approved supplier billed above the invoice is blocked, no money moves."""
    res = agent.settle(
        _pay("northstar_studio", 840.0, "ns-1", invoice_amount=480.0),
        event_id="s3",
    )
    assert res.decision == DecisionKind.BLOCK
    assert res.executed is False
    assert [c for c in agent.stripe.calls if c.op == "pay_supplier"] == []


def test_settle_blocks_duplicate_with_zero_rail_calls(agent):
    """A duplicate of an already-paid invoice is blocked on the second settle."""
    first = agent.settle(_pay("aws", 220.0, "aws-dup"), event_id="s4a")
    assert first.executed is True  # first one pays
    second = agent.settle(_pay("aws", 220.0, "aws-dup"), event_id="s4b")
    assert second.decision == DecisionKind.BLOCK
    assert second.executed is False
    # Only the first payment is on the rail; the duplicate never paid.
    pays = [c for c in agent.stripe.calls if c.op == "pay_supplier"]
    assert len(pays) == 1


def test_self_spend_over_budget_blocks_with_zero_rail_calls(agent):
    """An over-budget self-spend is blocked and provisions nothing."""
    agent.ctx.budget_remaining = 50.0
    agent.ctx.allowed_categories = {"fraud_detection"}
    spend = AgentEvent(kind=EventKind.SELF_SPEND, amount=5000.0,
                       category="fraud_detection", message="buy a big tool")
    res = agent.settle(spend, event_id="s5")
    assert res.decision == DecisionKind.BLOCK
    assert res.executed is False
    assert [c for c in agent.stripe.calls if c.op == "provision_capability"] == []


def test_approved_self_spend_provisions_on_the_rail(agent):
    """An in-budget, on-goal self-spend provisions exactly once via settle()."""
    agent.ctx.budget_remaining = 500.0
    agent.ctx.allowed_categories = {"fraud_detection"}
    spend = AgentEvent(kind=EventKind.SELF_SPEND, amount=120.0,
                       category="fraud_detection", message="fraud screening tool")
    res = agent.settle(spend, event_id="s6")
    assert res.decision == DecisionKind.APPROVE
    assert res.executed is True
    provisions = [c for c in agent.stripe.calls if c.op == "provision_capability"]
    assert len(provisions) == 1
    assert provisions[0].amount == 120.0


def test_stub_backend_reported_on_settlement(agent):
    """The settlement names its rail backend so a caller knows real vs recorded."""
    res = agent.settle(_pay("aws", 220.0, "aws-1"), event_id="s7")
    assert res.stripe_backend == "stub"
    # stub never fabricates a settlement id — None proves it was recorded, not real.
    assert res.stripe_id is None
