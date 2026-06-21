# LedgerGuard

**The Stripe accountant that does not trust itself with your money.**

Self-governing AI accountant / payment-ops agent for small operators.
Runs invoices, payment collection, reconciliation, fraud checks, and
self-funded reinvestment — but every money action passes through
deterministic policy, bounded Nemotron reasoning, and phone escalation.

Built for the Nous Research x NVIDIA x Stripe agent hackathon
(deadline EOD June 30, 2026).

## Why this wins

The field will build agents that spend money. We build the agent that can
be **trusted** with money. Governance is the product moat, not a footnote.

## Architecture (3 layers, money never decided by a raw LLM)

1. **Deterministic rules** — duplicate, amount-mismatch, vendor-detail-change,
   instruction-override, budget, off-goal. Hard pass or hard fail, no LLM.
2. **Bounded Nemotron reasoning** — strict JSON only. Nuance checks the rules
   can't express. Malformed output escalates. Never holds a Stripe tool.
3. **Phone escalation** — ambiguous money judgment goes to the owner.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # policy engine passes all 10 scenarios
python -m ledgerguard.cli    # plays the full demo timeline
```

## Repo layout

```
ledgerguard/
  models.py            # core dataclasses + enums
  policy/rules.py      # deterministic policy engine (the moat)
  agent/nemotron.py    # bounded LLM layer (mockable, strict JSON)
  agent/escalation.py  # phone escalation interface
  ledger/event_ledger.py  # append-only event log
  reinvest.py          # self-funded reinvest + self-guardrail
  stripe_glue.py       # thin interface Alex's webhook layer implements
  cli.py               # demo runner
scenarios/             # 10 JSON fixtures (the demo storyboard)
tests/                 # pytest proving policy engine passes every scenario
dashboard/             # Alex's lane — dashboard + phone approval UI
```

## Team lanes

- **Ben** — policy engine, agent core, ledger, reinvest, tests, demo runner; orchestration, narrative, submission, repo admin.
- **Alex** — Stripe Checkout/webhook, scenario generator, dashboard, phone UI.

## No real money

Test-mode only. No real charges, payouts, bank transfers, or real customer
emails. Stripe test keys live in `.env` (gitignored), never committed.
