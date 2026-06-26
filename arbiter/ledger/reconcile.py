"""Reconciliation: prove the Stripe rail matches the ledger's spend intent.

The ledger is the source of truth for what governance *approved*: every approved
self-spend or supplier payment adds to ``EventLedger.spend``. The Stripe rail is the
source of truth for what actually *settled*: an outbound call that did not fail.
``reconcile`` compares the two and reports any drift, so the demo (and a judge) can
see "the rail did exactly what the ledger says, to the penny" — or, when something
broke, see the gap instead of a masked success.

Pure function, no I/O: it reads an ``EventLedger`` and any ``StripeBackend`` (stub or
live). Outbound (spend) ops are ``pay_supplier`` and ``provision_capability``; inbound
money (``create_payment`` / ``create_checkout`` / ``webhook_received``) is not a spend
and is excluded. A call recorded ``failed=True`` (a live rail error caught by the glue
rather than raised — see B0) did not settle, so it is excluded from the settled total
and listed under ``failed_calls``.
"""

from __future__ import annotations

from typing import Any

# Stripe ops that move money OUT — the spend side reconciliation cares about.
_OUTBOUND_OPS = ("pay_supplier", "provision_capability")

# Penny tolerance: amounts are GBP floats; treat sub-penny gaps as exact to avoid
# float-representation noise being reported as drift.
_EPSILON = 0.005


def reconcile(ledger: Any, stripe: Any) -> dict:
    """Compare approved ledger spend against settled rail outflow.

    Returns a dict the dashboard binds to:
      * ``ledger_spend``  — total the ledger says was approved to spend.
      * ``rail_settled``  — total of non-failed outbound rail calls.
      * ``drift``         — abs(ledger_spend - rail_settled); 0.0 on a clean run.
      * ``ok``            — True iff drift is within a penny AND no failed calls.
      * ``failed_calls``  — outbound calls the rail recorded as failed (approved in
                            governance but never settled); the actionable list.
    """
    ledger_spend = round(float(getattr(ledger, "spend", 0.0)), 2)

    settled = 0.0
    failed_calls: list[dict] = []
    for c in getattr(stripe, "calls", []):
        if c.op not in _OUTBOUND_OPS:
            continue
        if getattr(c, "failed", False):
            failed_calls.append({
                "op": c.op,
                "payee": c.payee,
                "category": c.category,
                "amount": c.amount,
                "currency": c.currency,
                "notes": c.notes,
            })
            continue
        settled += c.amount or 0.0

    settled = round(settled, 2)
    drift = round(abs(ledger_spend - settled), 2)
    ok = drift <= _EPSILON and not failed_calls

    return {
        "ledger_spend": ledger_spend,
        "rail_settled": settled,
        "drift": drift,
        "ok": ok,
        "failed_calls": failed_calls,
    }
