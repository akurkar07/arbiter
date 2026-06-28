# Arbiter

**The money-operator for a real service business that refuses spend when it would destroy the margin.**

Arbiter runs a small service business end to end: it takes client invoices, buys
what each job needs to get delivered, and refuses any purchase that would make the
job unprofitable. Every money action passes through deterministic policy, bounded
NVIDIA Nemotron reasoning, and phone escalation to the owner. The agent never holds
the money rail directly — a single `settle()` door is the only path to a payment.

Built by **Ben Anokye-Davies** and **Alex Kurkar** for the Nous Research × NVIDIA × Stripe agent hackathon.

## The one thing to remember

> It refused to buy that because it would have made the job unprofitable.

The field is full of agents that *spend* money. Arbiter is the agent that **refuses
to** when the math doesn't work — and shows you the protected margin in an audit
ledger afterwards. Governance and margin protection are the product, not a footnote.

## Architecture — money is never decided by a raw LLM

Three layers, in order. The first hard verdict wins.

1. **Deterministic rules** (`arbiter/policy/rules.py`) — 14 first-match-wins rules:
   duplicate invoice, amount mismatch, vendor-detail change, instruction override
   (hard block), payee-not-approved, over-budget self-spend, off-goal spend. No LLM
   touches these. This is the moat.
2. **Bounded Nemotron reasoning** (`arbiter/agent/nim_nemotron.py`,
   `spend_judge.py`) — real NVIDIA NIM calls (Nemotron), strict-JSON only, for the
   nuance the rules can't express. Malformed or unreachable output escalates rather
   than guessing. Never holds a Stripe tool.
3. **Phone escalation** (`arbiter/agent/escalation.py`) — anything ambiguous goes
   to the owner for a yes/no tap. The human is the final layer, by design.

The single money door: the agent core holds no Stripe key. `ArbiterAgent.settle()`
is the only call that can move money, and it runs the full governance pipeline
before it does.

## Two demos

- **`/run_operator` — the autonomous money-operator (the headline).** Earns from
  client invoices, buys what each job needs, refuses spend that would kill the
  margin, escalates the edge cases to the owner by phone, and books the protected
  margin to the ledger. This is the one to watch.
- **`/run` — the AP-autopilot day.** Pay approved suppliers, block the rest,
  escalate the ambiguous. The original accounts-payable story.

## Real sponsor rails

| Sponsor | How it's used | State |
|---|---|---|
| **Stripe** | `LiveStripeGlue` moves real test-mode money via Connect Transfers (`tr_...`) + confirmed PaymentIntents (`pi_...`); activates on `STRIPE_SECRET_KEY` | Real (test mode) |
| **NVIDIA Nemotron** | Bounded spend/decision judgement over job constraints via NIM; OpenRouter fallback; activates on `NVIDIA_API_KEY` | Real |
| **Nous / Hermes** | MCP server (`arbiter/mcp_server.py`) exposes the governed `settle()` door; agent orchestration, tools, approvals, audit ledger | Real |

With no keys present, every rail falls back to a faithful mock so the suite and the
demo run offline. Boot banners say which mode is live, so a recorded demo can
truthfully claim real sponsor tech.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # full test suite
python -m arbiter.cli         # plays the operator timeline in the terminal
```

For the dashboard demo, serve the web app and open `dashboard/dashboard.html`:

```bash
uvicorn arbiter.web.server:app --port 8000
# then open the dashboard and press "Go live"
```

Real rails need test-mode keys (see `.env.example`); without them the demo runs
mocked.

## Repo layout

```
arbiter/
  models.py              # core dataclasses + enums
  policy/rules.py        # 14 deterministic rules (the moat)
  agent/
    agent.py             # 3-layer core + settle() single money door
    nemotron.py          # bounded LLM base (strict JSON)
    nim_nemotron.py      # real NVIDIA NIM client (+ OpenRouter fallback)
    spend_judge.py       # margin-aware spend judgement
    escalation.py        # phone escalation interface
  ledger/event_ledger.py # append-only ledger, dashboard-ready timeline
  operator.py            # autonomous money-operator loop (earn -> spend -> protect)
  business_day.py        # the AP-autopilot scripted day
  ingest/                # invoice ingestion (vision extract -> governed decision)
  stripe_glue.py         # StripeGlue stub + LiveStripeGlue (real Connect Transfers)
  metrics.py             # run rollups
  web/server.py          # FastAPI: /run, /run_operator, /authorize, /state, /reset
  mcp_server.py          # Hermes MCP server exposing the governed door
  cli.py                 # demo runner
scenarios/               # 10 JSON fixtures (the AP demo storyboard)
dashboard/               # dashboard + phone approval UI
tests/                   # policy, operator, ingest, web, single-door
docs/                    # architecture diagrams and public specs
```

## Team

**Ben Anokye-Davies** and **Alex Kurkar** built Arbiter together.

- **Ben Anokye-Davies** — backend policy engine, agent core, ledger, operator loop, governed payment flow, tests, demo narrative, and submission.
- **Alex Kurkar** — dashboard/front-end experience, phone approval surface, visual demo flow, and product presentation polish.

## No real money

Test mode only. No real charges, payouts, bank transfers, or customer emails.
Stripe test keys live in `.env` / `arbiter.env` (gitignored), never committed.
