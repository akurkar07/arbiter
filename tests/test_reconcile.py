"""F5-lite — reconciliation: does the rail actually match the ledger?

The ledger records governance *intent*: every APPROVED spend (self-spend or
supplier payment) adds to ``ledger.spend``. The Stripe rail records what actually
*settled*: an outbound call (``pay_supplier`` / ``provision_capability``) that did
not fail. Reconciliation proves those two agree — and flags drift when they don't.

This is the natural consumer of the B0 fix: a live transfer that errored is now
recorded ``failed=True`` and excluded from the settled total, so an approved-but-
unsettled payment shows up as drift here instead of being silently counted as moved.
That is the "did the rail do what the ledger says?" loop a judge asks about.
"""

from arbiter.ledger.event_ledger import EventLedger
from arbiter.ledger.reconcile import reconcile
from arbiter.models import AgentEvent, EventKind, DecisionKind, PolicyResult, DecisionLayer
from arbiter.stripe_glue import StripeGlue, StripeCall


def _approved(kind, amount, **kw):
    ev = AgentEvent(kind=kind, amount=amount, currency="GBP", **kw)
    res = PolicyResult(decision=DecisionKind.APPROVE, reason="ok",
                       policy_refs=["x"], risk_score=0.0, decided_by=DecisionLayer.RULES)
    return ev, res


def test_reconcile_clean_run_has_zero_drift():
    """Every approved spend has a matching non-failed rail call -> reconciled."""
    ledger = EventLedger()
    stripe = StripeGlue()
    # Two approved supplier payments, both settle on the (stub) rail.
    for i, amt in enumerate((220.0, 90.0)):
        ev, res = _approved(EventKind.VENDOR_PAYMENT, amt, vendor_id=f"v{i}")
        ledger.record(ev, res, event_id=f"e{i}")
        stripe.pay_supplier(f"v{i}", amt)

    rec = reconcile(ledger, stripe)
    assert rec["ledger_spend"] == 310.0
    assert rec["rail_settled"] == 310.0
    assert rec["drift"] == 0.0
    assert rec["ok"] is True
    assert rec["failed_calls"] == []


def test_reconcile_flags_drift_when_a_rail_call_failed():
    """An approved spend whose rail call failed must surface as drift, not be hidden."""
    ledger = EventLedger()
    stripe = StripeGlue()
    # Approved in governance...
    ev, res = _approved(EventKind.VENDOR_PAYMENT, 220.0, vendor_id="aws")
    ledger.record(ev, res, event_id="e1")
    # ...but the rail call errored (B0 records it failed=True, no settlement).
    stripe.calls.append(StripeCall(op="pay_supplier", payee="aws", amount=220.0,
                                   notes="live call failed", failed=True))

    rec = reconcile(ledger, stripe)
    assert rec["ledger_spend"] == 220.0
    assert rec["rail_settled"] == 0.0      # nothing actually moved
    assert rec["drift"] == 220.0           # the gap is visible
    assert rec["ok"] is False
    assert len(rec["failed_calls"]) == 1
    assert rec["failed_calls"][0]["amount"] == 220.0


def test_reconcile_ignores_inbound_calls():
    """Inbound money (create_payment / checkout) is not a spend; it must not count
    toward the settled-spend total."""
    ledger = EventLedger()
    stripe = StripeGlue()
    ev, res = _approved(EventKind.SELF_SPEND, 35.0, category="compute")
    ledger.record(ev, res, event_id="e1")
    stripe.provision_capability("compute", 35.0)
    # Inbound earn — must be ignored by spend reconciliation.
    stripe.create_payment("inv-1", 500.0)

    rec = reconcile(ledger, stripe)
    assert rec["ledger_spend"] == 35.0
    assert rec["rail_settled"] == 35.0
    assert rec["ok"] is True
