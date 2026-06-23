"""The business-day demo — one coherent AP-autopilot story, real engine.

This is the spine of the pitch. Not a grab-bag of rule tests: a single business
day for a small studio that connected Stripe and approved three suppliers
(aws, northstar_studio, acme_print). Revenue lands, then Arbiter works the
payables queue. Every decision below comes from the REAL policy engine governed
by the REAL owner allowlist — nothing here is scripted to a verdict.

Run:
    python -m arbiter.business_day            # human-readable
    python -m arbiter.business_day --json     # timeline JSON for the dashboard

The narrative beats, in order:
  1. Revenue in: a customer pays a £480 invoice (earn).            -> APPROVE
  2. Pay AWS, an approved+established supplier, correct amount.    -> APPROVE (autopilot pays)
  3. Pay Acme Print, approved+established, correct amount.         -> APPROVE (autopilot pays)
  4. A second AWS charge, identical ref+amount — already paid.     -> BLOCK  (duplicate)
  5. "Northstar" invoice for £840 but the invoice on file is £480. -> BLOCK  (amount mismatch)
  6. A payment to Meta Ads — NOT on the approved list.             -> BLOCK  (payee not approved)  <-- the answer to "it can pay anyone"
  7. Northstar asks to change bank details, weak evidence.         -> ESCALATE (owner phone tap)

That is: it does its job (pays the suppliers you approved), and it protects you
(blocks the double-pay, the overpay, the stranger, and asks you when unsure).
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import ArbiterAgent
from .agent.escalation import HoldEscalation
from .agent.nim_nemotron import select_nemotron
from .ledger import EventLedger
from .models import AgentEvent, EventKind, DecisionKind
from .policy.config import demo_policy_context
from .stripe_glue import select_stripe


def business_day_events() -> list[tuple[str, str, AgentEvent, list[tuple[str, float, str]]]]:
    """The day's queue: (event_id, beat, event, fingerprint_seeds).

    Fingerprint seeds represent payments already made earlier in the cycle —
    they prime the duplicate detector for beat 4.
    """
    return [
        (
            "01_revenue_in",
            "Revenue in: Brightwave pays their £480 invoice. Reconciled, no red flags.",
            AgentEvent(kind=EventKind.INVOICE_PAYMENT, vendor_id="cust_brightwave",
                       invoice_id="inv_1001", ref="INV-1001", amount=480.0,
                       invoice_amount=480.0, vendor_known=True, vendor_history_count=4),
            [],
        ),
        (
            "02_pay_aws",
            "Pay AWS £220 — approved supplier, monthly cloud bill, amount matches.",
            AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="aws",
                       invoice_id="inv_aws_06", ref="AWS-06", amount=220.0,
                       invoice_amount=220.0, vendor_known=True, vendor_history_count=11),
            [],
        ),
        (
            "03_pay_acme",
            "Pay Acme Print £140 — approved supplier, this month's print run, matches.",
            AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="acme_print",
                       invoice_id="inv_acme_06", ref="ACME-06", amount=140.0,
                       invoice_amount=140.0, vendor_known=True, vendor_history_count=7),
            [],
        ),
        (
            "04_aws_duplicate",
            "A second identical AWS charge lands — same ref, same £220. Already paid.",
            AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="aws",
                       invoice_id="inv_aws_06", ref="AWS-06", amount=220.0,
                       invoice_amount=220.0, vendor_known=True, vendor_history_count=11),
            [("aws", 220.0, "AWS-06")],  # beat 2 already paid this
        ),
        (
            "05_northstar_overpay",
            "An invoice claims Northstar is owed £840 — but the invoice on file is £480.",
            AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="northstar_studio",
                       invoice_id="inv_ns_06", ref="NS-06", amount=840.0,
                       invoice_amount=480.0, vendor_known=True, vendor_history_count=5),
            [],
        ),
        (
            "06_unapproved_payee",
            "A £300 invoice from Meta Ads arrives — a payee the owner never approved.",
            AgentEvent(kind=EventKind.VENDOR_PAYMENT, vendor_id="meta_ads",
                       invoice_id="inv_meta_01", ref="META-01", amount=300.0,
                       invoice_amount=300.0, vendor_known=True, vendor_history_count=0),
            [],
        ),
        (
            "07_northstar_bank_change",
            "Northstar emails: 'new bank details, please update.' Evidence is weak.",
            AgentEvent(kind=EventKind.VENDOR_DETAIL_CHANGE, vendor_id="northstar_studio",
                       invoice_id="inv_ns_06", ref="NS-06", vendor_known=True,
                       vendor_history_count=5, detail_change_evidence=0.3,
                       message="Hi, we changed banks — pay the new account from now on."),
            [],
        ),
    ]


def run(interactive: bool = False) -> dict:
    """Play the business day through the real engine + allowlist. Returns timeline dict."""
    ctx = demo_policy_context()  # the 3-supplier allowlist
    ledger = EventLedger()
    stripe = select_stripe()  # real test-mode rail if STRIPE_SECRET_KEY set, else stub
    agent = ArbiterAgent(
        ctx=ctx,
        ledger=ledger,
        nemotron=select_nemotron(),
        escalation=HoldEscalation(),  # leave escalations pending for the owner tap
    )

    events = business_day_events()
    approved = sorted(ctx.approved_payees) if ctx.approved_payees else []
    print("=" * 76)
    print(f"Arbiter — a business day on autopilot. Owner approved: {approved}")
    print("=" * 76)

    tally = {"approve": 0, "block": 0, "escalate": 0}
    for i, (event_id, beat, event, seeds) in enumerate(events, 1):
        for fp in seeds:
            ctx.recent_payment_fingerprints.add(fp)
        result = agent.decide(event, event_id=event_id, demo_beat=beat)
        tally[result.decision.value] = tally.get(result.decision.value, 0) + 1

        # Money only moves on an APPROVED decision — the rail is never touched
        # for a blocked or escalated event. This is the governance->rail seam:
        # the engine decides, and only an approval reaches Stripe.
        if result.decision == DecisionKind.APPROVE:
            if event.kind == EventKind.VENDOR_PAYMENT and event.vendor_id:
                stripe.pay_supplier(event.vendor_id, event.amount or 0.0,
                                    event.currency, ref=event.ref)
            elif event.kind == EventKind.INVOICE_PAYMENT:
                stripe.create_checkout(event.ref or "n/a", event.amount or 0.0, event.currency)
                stripe.webhook_received("checkout.session.completed", event.ref)

        tag = {"approve": "PAY  ", "block": "BLOCK", "escalate": "ASK  "}[result.decision.value]
        print(f"[{i}/{len(events)}] {tag} | {beat}")
        print(f"         -> {result.reason[:150]}")

    paid_calls = [c for c in stripe.calls if c.op == "pay_supplier"]
    print("=" * 76)
    print(f"Paid: {tally['approve']}   Blocked: {tally['block']}   "
          f"Owner asked: {tally['escalate']}")
    print(f"Earnings: {ledger.earnings:.2f}   Spend: {ledger.spend:.2f}   "
          f"Net: {ledger.net:+.2f}")
    print(f"Stripe [{stripe.backend}]: {len(paid_calls)} supplier payment(s) "
          f"-> {[f'{c.payee} {c.amount:.0f}' + (f' {c.stripe_id}' if c.stripe_id else '') for c in paid_calls]}")
    print("=" * 76)
    return {"timeline": ledger.as_timeline(), "tally": tally,
            "earnings": ledger.earnings, "spend": ledger.spend, "net": ledger.net,
            "stripe_backend": stripe.backend,
            "stripe_calls": [c.__dict__ for c in stripe.calls]}


def main() -> int:
    p = argparse.ArgumentParser(prog="arbiter-business-day",
                                description="Run the Arbiter AP-autopilot business day.")
    p.add_argument("--interactive", action="store_true", help="prompt y/n on each escalation")
    p.add_argument("--json", action="store_true", help="emit the timeline as JSON")
    args = p.parse_args()
    out = run(interactive=args.interactive)
    if args.json:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
