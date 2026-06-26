"""B0 — settle() must not report executed=True when the live rail call failed.

The honesty bug: settle() set ``executed=True`` whenever ``_execute()`` returned a
StripeCall, without checking whether that call actually settled. The live glue
*records* a call (rather than raising) when the Stripe API errors, so an approved
payment whose rail call failed — no ``tr_/pi_`` id produced — was reported as
``executed=True``. That lies to reconciliation and to a judge.

The fix distinguishes three cases the suite must keep straight:

  * stub backend: no real id is ever produced, yet the decision legitimately
    "executed" (the demo's recorded money movement) — must stay executed=True;
  * live success: a real id is produced — executed=True;
  * live FAILURE: the call is recorded with ``failed=True`` and no id — executed
    MUST be False.

So the signal is not "is there an id" (the stub and the inbound webhook never carry
one); it is "did the rail call fail". These tests lock that.
"""

from arbiter.agent import ArbiterAgent
from arbiter.models import AgentEvent, EventKind, DecisionKind
from arbiter.policy.config import demo_policy_context
from arbiter.stripe_glue import StripeGlue, StripeCall


class _FailingRailGlue(StripeGlue):
    """A stub that mimics the LIVE glue's except-branch: an approved payment whose
    rail call errored is *recorded with failed=True* and no stripe_id, never raised.
    """

    backend = "live-test"

    def pay_supplier(self, payee, amount, currency="GBP", ref=None):
        c = StripeCall(
            op="pay_supplier", payee=payee, amount=amount, currency=currency, ref=ref,
            notes="live call failed, recorded only: APIError: transfers not active",
            failed=True,
        )
        self.calls.append(c)
        return c


def _pay(vendor_id, amount, ref):
    return AgentEvent(
        kind=EventKind.VENDOR_PAYMENT, vendor_id=vendor_id, amount=amount,
        invoice_amount=amount, currency="GBP", vendor_known=True,
        vendor_history_count=11, ref=ref,
    )


def test_settle_reports_not_executed_when_live_rail_call_failed():
    """An APPROVE whose live rail call failed must settle executed=False."""
    agent = ArbiterAgent(ctx=demo_policy_context(), stripe=_FailingRailGlue())
    res = agent.settle(_pay("aws", 220.0, "aws-fail"), event_id="bf1")

    assert res.decision == DecisionKind.APPROVE  # governance still approved
    # The rail call was attempted and recorded...
    pays = [c for c in agent.stripe.calls if c.op == "pay_supplier"]
    assert len(pays) == 1 and pays[0].failed is True
    # ...but it did NOT settle, so the settlement must not claim it did.
    assert res.executed is False
    assert res.stripe_id is None
    assert res.moved_money is False


def test_stub_success_still_executes():
    """Guard against over-correction: a stub approve still reports executed=True
    even though the stub never produces a stripe_id."""
    agent = ArbiterAgent(ctx=demo_policy_context(), stripe=StripeGlue())
    res = agent.settle(_pay("aws", 220.0, "aws-ok"), event_id="bf2")
    assert res.decision == DecisionKind.APPROVE
    assert res.executed is True       # recorded money movement counts as executed
    assert res.stripe_id is None      # stub legitimately has no real id
