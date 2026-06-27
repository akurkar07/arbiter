# PROJECT.md — Arbiter

## Purpose

Arbiter is an autonomous money-operator for a real service business.

Core promise:

> AI can recommend. Policy can refuse. The ledger proves it.

The product is not "an AI with a Stripe key". The product is controlled financial operations: owner policy, deterministic governance, bounded AI judgement, owner escalation, rail reconciliation, and an evidence ledger.

## Current architecture

### Source package

- `arbiter/` is the Python source package. Do not move it to `src/` before the hackathon submission unless there is a specific reason and time to run the full test suite.
- `pyproject.toml` currently uses setuptools package discovery with `include = ["arbiter*"]`.

### Key paths

- `arbiter/models.py` — dataclasses/enums: `AgentEvent`, `PolicyContext`, `PolicyResult`, `SettlementResult`.
- `arbiter/policy/rules.py` — deterministic policy moat, first matching rule wins, unknown event escalates.
- `arbiter/policy/config.py` — owner/business policy config -> `PolicyContext`.
- `arbiter/agent/agent.py` — `decide()` and `settle()`. `settle()` is the single money door.
- `arbiter/operator.py` — service-business loop: earn -> verify -> budget -> spend/refuse -> per-job ledger.
- `arbiter/procurement.py` — F3 procurement scout. Scout sources, policy still decides.
- `arbiter/ledger/event_ledger.py` — timeline/audit evidence for dashboard.
- `arbiter/stripe_glue.py` — Stripe stub/live rail selection.
- `arbiter/web/server.py` — FastAPI backend: `/state`, `/run`, `/run_operator`, `/reset`, approve/deny, `/authorize`.
- `dashboard/` — static dashboard: `dashboard.html`, `app.js`, `styles.css`, sample state.
- `docs/` — repo-local docs, plans, handoffs, diagrams, specs.
- `tests/` — policy, operator, web, single-door, procurement, reconciliation tests.

## Money path

```text
BusinessOperator.run_job(job)
  -> client payment / webhook
  -> AgentEvent(INVOICE_PAYMENT)
  -> ArbiterAgent.decide()
  -> policy.rules.evaluate(event, PolicyContext)
  -> ledger records invoice decision
  -> needed spend becomes AgentEvent(SELF_SPEND)
  -> ArbiterAgent.settle()
  -> decide first
  -> execute Stripe only if APPROVE
  -> SettlementResult + ledger prove decision and execution truth
```

Important distinction:

```text
decide() = judgement, no money movement
settle() = judgement, then execution only if APPROVE
```

No feature should create a second payment path outside `settle()`.

## External systems

- Stripe test mode: PaymentIntents / Connect Transfers when `STRIPE_SECRET_KEY` is present. Stub otherwise.
- NVIDIA NIM / Nemotron: bounded reasoning and spend judgement when `NVIDIA_API_KEY` is present. Mock/fallback otherwise.
- Nous/Hermes: MCP server and agent integration through governed `/authorize`/settle path.

No real money in this repo. Test mode only.

## Current demo spine

```text
Owner sets policy
-> owner chooses trust mode
-> client pays through Stripe
-> Arbiter verifies revenue
-> AI suggests spend
-> deterministic policy checks the owner's rules
-> trust mode controls execution
-> rail reconciliation proves whether money moved
-> ledger/audit trail records everything
```

Signature line:

> It refused to buy that because it would have made the job unprofitable.

## Current feature planning

Repo-local docs:

- `docs/plans/ARBITER_FEATURE_NOTES_2026-06-27.md`
- `docs/handoffs/ARBITER_ALEX_FEATURE_PLAN_2026-06-27.md`

Core features under consideration:

1. Owner Policy Setup
2. Decision Receipts
3. Trust Controls / Safe Autonomy Mode
4. Audit Trail / Evidence Panel
5. Counterparty Review + Payables Queue
6. Rule Packs / Policy Profiles
7. Policy Replay / What-If Simulator
8. Double-Spend Shield / Budget Reservation
9. Adversarial Spend Test / Red-Team Mode
10. Rail Reconciliation / Execution Truth Monitor

Do not build all ten equally. Demo core likely centres on Owner Policy Setup, Decision Receipts, Trust Controls, Policy Replay, Red-Team Mode, and Rail Reconciliation/Audit Evidence.

## Verification commands

Clean/offline verification:

```powershell
pytest
```

If live env vars are exported and tests flip into real-rail paths, run with relevant keys unset for hermetic tests.

Dashboard/backend:

```powershell
uvicorn arbiter.web.server:app --host 127.0.0.1 --port 8000
```

Then open the served dashboard and use Run demo / Go live.

## Safety rules

- No money path outside `ArbiterAgent.settle()`.
- Frontend does not decide safety. It displays backend decisions.
- Receipts must derive from policy refs/reasons, not AI-written post-hoc explanations.
- Do not imply real outbound money moved if the rail is stubbed.
- Trust/autonomy controls can only make execution stricter, never override a block.
- Unknown events escalate by default.

## Branch cleanup context

See `docs/repo-cleanup/BRANCH_CLEANUP_2026-06-27.md` before deleting old branches.
