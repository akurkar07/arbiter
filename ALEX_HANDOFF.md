# Alex — handoff & interface

**Repo:** https://github.com/benedict-anokye-davies/arbiter (you're a collaborator)
**Hackathon:** Nous × NVIDIA × Stripe — deadline EOD June 30, 2026
**Team:** Ben (agent core + governance, orchestrate, present) · Alex (Stripe + dashboard + phone UI)

> **Your current task list is `docs/handoffs/ALEX_TASKS_2026-06-26.md`** — start
> there. This file is the standing interface reference.

---

## Where the project actually is (June 26)

The whole engine side is built, live, and tested — **129 passing**:

- **Governance core** — 14 deterministic rules, 3-layer pipeline (rules → bounded
  Nemotron → phone escalation), `settle()` single money door. Done.
- **Real sponsor rails** — `LiveStripeGlue` (Connect Transfers, real test-mode
  `tr_`/`pi_` ids), real NVIDIA NIM via `nim_nemotron.py` + `spend_judge.py`, Hermes
  MCP server. All auto-activate when keys are present; fall back to faithful mocks
  otherwise.
- **Autonomous operator** (`operator.py`) — the headline demo: earn from invoices,
  buy what each job needs, refuse margin-killing spend, escalate edge cases, book
  protected margin. Driven by `POST /run_operator`.
- **Invoice ingestion** (`ingest/`) — drop a PDF/image invoice, vision-extract,
  feed the governed pipeline.
- **Web server** (`web/server.py`) — `/run`, `/run_operator`, `/authorize`,
  `/state`, `/reset`.

The timeline now stamps `job` + `margin_killer` on every row, so your existing
`isHero()` / per-job grouping fire on **live** data, not just `sample_state.json`.

---

## Your interface — what you own

### 1. Dashboard (`dashboard/`)
The live story comes from `GET /state`. Press "Go live" → it POSTs `/run_operator`
and polls `/state`. Each timeline row carries: `id, kind, decision, reason, refs,
risk, layer, amount, beat, job, margin_killer`. The business rollup is under
`state.business` (jobs, spends, judgements, `net_profit`, `waste_blocked`, margin
figures).

### 2. Stripe surface
`LiveStripeGlue` already moves test-mode money. Your job is to **surface** the real
`tr_`/`pi_` ids in the UI and build the webhook receiver if we want live
invoice-in. The agent core calls `settle()`; you never need to add a payment path —
if you think you do, that's a red flag, ask first.

### 3. Phone escalation UI
The backend parks on `awaiting_approval` with `approve_url` / `deny_url`. Replace
the desktop approve button with a phone-framed surface (Twilio SMS tap-to-approve is
enough). The core just needs a yes/no back.

---

## What you do NOT touch
`arbiter/policy/`, `arbiter/agent/`, `arbiter/operator.py`, `arbiter/ledger/` — the
governance core, done and tested. Need a new field? Ask and I'll publish it on
`/state`; you bind it. Keeps your diffs in the dashboard and out of merge-conflict
range with the core.

---

## Integration point (if you wire a live webhook)
```python
from arbiter.agent import ArbiterAgent
from arbiter.models import AgentEvent, EventKind, PolicyContext

agent = ArbiterAgent(ctx=PolicyContext())
event = AgentEvent(kind=EventKind.INVOICE_PAYMENT, vendor_id="cust_x", ...)
result = agent.settle(event, event_id="evt_001", demo_beat="Customer paid invoice")
# result.decision == APPROVE | BLOCK | ESCALATE; ledger already recorded it
```
