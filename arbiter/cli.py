"""Demo CLI runner — plays the full Arbiter timeline from the scenario set.

One command runs the complete demo storyboard:
  earn -> operate -> block fraud -> escalate -> reinvest -> improve -> self-block

    python -m arbiter.cli            # full timeline, auto-approve escalations
    python -m arbiter.cli --interactive   # y/n on each escalation
    python -m arbiter.cli --json          # emit the ledger timeline as JSON
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import ArbiterAgent, ConsoleEscalation
from .agent.nim_nemotron import select_nemotron
from .ledger import EventLedger
from .models import AgentEvent, EventKind, PolicyContext, DecisionKind
from .reinvest import maybe_reinvest_event, REINVEST_THRESHOLD
from .metrics import reinvest_improvement
from .scenarios import load_scenario, list_scenarios
from .stripe_glue import StripeGlue


def _print_beat(idx: int, total: int, beat: str, decision: str, reason: str) -> None:
    tag = {"approve": "OK  ", "block": "STOP", "escalate": "ASK "}.get(decision, decision[:4].upper())
    print(f"[{idx}/{total}] {tag} | {beat}")
    print(f"       -> {reason[:160]}")


def run(interactive: bool = False) -> dict:
    """Run the full demo timeline. Returns the ledger timeline as a dict."""
    ctx = PolicyContext()
    ledger = EventLedger()
    agent = ArbiterAgent(
        ctx=ctx,
        ledger=ledger,
        nemotron=select_nemotron(),
        escalation=ConsoleEscalation(auto=not interactive),
    )
    stripe = StripeGlue()

    names = list_scenarios()
    total = len(names)
    print("=" * 72)
    print("Arbiter — self-governing AI accountant. Demo timeline.")
    print("=" * 72)

    for i, name in enumerate(names, 1):
        event, _expected, raw = load_scenario(name)
        beat = raw.get("demo_beat", name)

        # Apply per-scenario context seeds (e.g. fingerprints for duplicate detection).
        for fp in raw.get("seed_fingerprints", []):
            ctx.recent_payment_fingerprints.add((fp[0], fp[1], fp[2]))

        # earn beat: simulate the Stripe checkout webhook before the invoice reconciliation
        if event.kind == EventKind.INVOICE_PAYMENT:
            stripe.create_checkout(event.ref or "n/a", event.amount or 0, event.currency)
            stripe.webhook_received("checkout.session.completed", event.ref)

        result = agent.decide(event, event_id=name, demo_beat=beat)
        _print_beat(i, total, beat, result.decision.value, result.reason)

    print("=" * 72)
    print(f"Earnings:   {ledger.earnings:.2f}")
    print(f"Spend:      {ledger.spend:.2f}")
    print(f"Net:        {ledger.net:+.2f}")
    print(f"Blocks:     {len(ledger.blocks())}")
    print(f"Escalations:{len(ledger.escalations())}")
    # The reinvest beat's honest, measured before/after — computed by re-running
    # the real agent over the fraud scenario set, not asserted constants.
    gov = reinvest_improvement()
    b, a = gov["before"], gov["after"]
    print("-" * 72)
    print("Governance (measured over the fraud scenario set):")
    print(f"  Fraud never auto-paid (catch-rate):  {b['catch_rate']:.0%}  ->  {a['catch_rate']:.0%}")
    print(
        f"  Resolved without a human (autonomy): {b['autonomous_rate']:.0%}  ->  {a['autonomous_rate']:.0%}"
        f"   (+{gov['autonomy_gain']:.0%} after reinvest)"
    )
    print(
        f"  Owner taps needed:                   {b['escalated']}  ->  {a['escalated']}"
        "   (bank-reconciliation resolves the known-vendor change autonomously)"
    )
    print("=" * 72)
    return {"timeline": ledger.as_timeline(), "stripe_calls": [c.__dict__ for c in stripe.calls], "governance": gov}


def main() -> int:
    p = argparse.ArgumentParser(prog="arbiter-demo", description="Run the Arbiter demo timeline.")
    p.add_argument("--interactive", action="store_true", help="prompt y/n on each escalation")
    p.add_argument("--json", action="store_true", help="emit the timeline as JSON instead of human-readable")
    p.add_argument("--selftest", action="store_true",
                   help="prove the live NVIDIA NIM Nemotron path with one real call, then exit")
    args = p.parse_args()

    if args.selftest:
        from .agent.nim_nemotron import selftest
        return selftest()

    if args.json:
        out = run(interactive=False)
        print(json.dumps(out, indent=2))
        return 0
    run(interactive=args.interactive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
