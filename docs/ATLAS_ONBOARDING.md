# Arbiter — Onboarding for Atlas

**Author:** Helios · **Date:** 2026-06-26 · **Audience:** Atlas (taking over planning/orchestration)
**Purpose:** Everything you need to understand Arbiter, find it, run it, and decide how to work on it.

---

## 0. TL;DR — can you pull it locally, or must you SSH?

**Both, depending on what you need:**

| What you want | Where it is | How to get it |
|---|---|---|
| The **baseline** project (everything merged to `main`) | `github.com/benedict-anokye-davies/arbiter` @ `72cf883` | `git clone` locally — works today, no SSH |
| **This session's work** (F1, B0, F4-lite, F5-lite — 3 commits) | **VPS only**, branch `feat/f1-nemotron3-default` @ `c0c35a1` | **NOT on GitHub.** Needs a push (Ben's call) OR SSH into helios-prod |
| The **v3 expansion plan + procurement diagram + hash-chain spec** | **VPS only**, untracked files under `docs/` | Same — not pushed; SSH or wait for a commit+push |
| The **live `.env` with real test-mode keys** | `/root/arbiter.env` on helios-prod, `chmod 600`, gitignored | SSH only — by design never leaves the box |

**Bottom line:** A plain `git clone` gives you the project as of the last merge to `main`, which is **fully functional and runs mocked with zero keys** — you can read, run, and test 100% of the architecture locally right now. But the **latest 3 commits and the current plan are trapped on the VPS** until Ben approves a push. If you want to review *those* without a push, you SSH in. If you want to run the **real-money (test-mode) Stripe + live Nemotron** rails, you SSH in regardless, because the keys live only on the box.

**My recommendation:** ask Ben to let me **push `feat/f1-nemotron3-default`** so you can review the real diffs locally. Everything except the keys becomes clonable, and you only SSH when you need the live rails. Until then, this document + the baseline clone is enough to plan against.

---

## 1. What Arbiter is

**The money-operator for a real service business that refuses spend when it would destroy the margin.**

The field is full of agents that *spend* money. Arbiter is the agent that **refuses to** when the math doesn't work. One line to remember:

> "It refused to buy that because it would have made the job unprofitable."

Built for the **Nous × NVIDIA × Stripe** hackathon. **Deadline: EOD 2026-06-30.**

### The core guarantee — money is never decided by a raw LLM

Three layers, first hard verdict wins:

1. **Deterministic rules** (`arbiter/policy/rules.py`) — 14 first-match-wins rules (duplicate invoice, amount mismatch, vendor-detail change, instruction-override hard block, payee-not-approved, over-budget self-spend, off-goal spend). No LLM touches these. **This is the moat.**
2. **Bounded Nemotron reasoning** (`arbiter/agent/nim_nemotron.py`, `spend_judge.py`) — real NVIDIA NIM calls, strict-JSON only, for nuance rules can't express. Malformed/unreachable → **escalate, never guess**. Never holds a Stripe tool.
3. **Phone escalation** (`arbiter/agent/escalation.py`) — anything ambiguous → owner yes/no tap. The human is the final layer.

**The single money door:** the agent core holds no Stripe key. `ArbiterAgent.settle()` is the only call that can move money, and it runs the full pipeline first. This inversion is the whole product — see `tests/test_settle_single_door.py`.

---

## 2. Where it lives (exact coordinates)

- **Host:** `helios-prod` — `91.107.237.193` — user `root`
- **Repo path on VPS:** `/root/helios-workspace/arbiter`
- **GitHub:** `https://github.com/benedict-anokye-davies/arbiter.git`
- **Commits land as:** Ben (`benedict-anokye-davies` / `benanokye577@gmail.com`)
- **Live keys (SSH-only, never committed):** `/root/arbiter.env` (`chmod 600`)

### Branch / sync state (verified 2026-06-26)

```
origin/main                       72cf883   <- baseline, what `git clone` gives you
origin/alex/dashboard-build       ababeee   <- Alex's lane
origin/feat/hermes-native-mcp-...  80a0c17  <- prior MCP work

LOCAL ON VPS ONLY (not pushed):
feat/f1-nemotron3-default         c0c35a1   <- THIS SESSION'S WORK, 3 commits ahead of main
```

The 3 unpushed commits:
- `eb66196` F1 — code defaults → current-gen Nemotron 3
- `7b10bcd` B0 — `settle()` no longer reports `executed=True` when a live rail call failed (honesty bug; RED→GREEN tests)
- `c0c35a1` F4-lite + F5-lite — real Stripe `pi_`/`tr_` ids + reconciliation block on `/state`

**Untracked (local-only) planning docs:** `docs/FEATURE_EXPANSION_PLAN_V2.md` (the v3 plan), `docs/diagrams/procurement_through_the_gate.mmd`, `docs/specs/HASHCHAIN_LEDGER_SPEC.md`. Also `state/` is gitignored by design (review packets, artifacts) — local working state, not source.

---

## 3. How to work on it — two paths

### Path A: clone locally (review + offline dev)

```bash
git clone https://github.com/benedict-anokye-davies/arbiter.git
cd arbiter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,stripe,llm,web,mcp]"   # all extras; or ".[dev]" for just the suite
pytest                                         # baseline suite passes mocked, zero keys
python -m arbiter.cli                          # plays the operator timeline in the terminal
```

This gives you `main` @ `72cf883`. **You will NOT see F1/B0/F4-lite/F5-lite or the v3 plan** — those are unpushed. Everything runs mocked (faithful stubs) with no keys, so the full architecture is explorable offline.

**Dependencies** are all optional extras (core `dependencies = []`): `dev` (pytest), `stripe` (stripe SDK — needed or 5 live-glue tests error on import), `llm` (openai SDK for NIM), `web` (fastapi/uvicorn), `mcp` (mcp/httpx). Python ≥ 3.10.

### Path B: SSH into helios-prod (current work + live rails)

```bash
ssh root@91.107.237.193
cd /root/helios-workspace/arbiter
source .venv/bin/activate
git checkout feat/f1-nemotron3-default        # the current work
set -a; . /root/arbiter.env; set +a           # load real test-mode keys
pytest                                         # 136 pass (clean env; see note below)
```

**You need Path B if you want to:** review the unpushed commits/plan without a push, OR run the real Stripe test-mode + live Nemotron rails (keys are on the box only).

> ⚠️ **Test-env gotcha (real, will bite you):** the suite is **136 green in a *clean* env**, but if `STRIPE_SECRET_KEY` is exported in your shell, two web tests (`test_authorize`, `test_invoice_ingestion`) flip to the **live** rail where the test account's Connect transfer fails → 2 failures. That's not a regression, it's a non-hermetic-test issue (logged as a B0 follow-up). Run the suite with `env -u STRIPE_SECRET_KEY -u NVIDIA_API_KEY pytest` for the true green, or just `pytest` in a shell that hasn't sourced `arbiter.env`.

---

## 4. Repo layout (what's where)

```
arbiter/                    # 4,635 LOC core package
  models.py                 # dataclasses + enums (AgentEvent, PolicyResult, SettlementResult, StripeCall)
  policy/rules.py           # 14 deterministic rules — THE MOAT
  policy/config.py          # demo policy context (allowlist, budget, categories)
  agent/
    agent.py                # 3-layer core + settle() single money door
    nemotron.py             # bounded LLM base (strict JSON)
    nim_nemotron.py         # real NVIDIA NIM client (+ OpenRouter fallback)
    spend_judge.py          # margin-aware spend judgement
    escalation.py           # phone escalation interface
  ledger/
    event_ledger.py         # append-only ledger, dashboard-ready timeline
    reconcile.py            # NEW (F5-lite) — rail-vs-ledger reconciliation
  operator.py               # autonomous money-operator loop (earn -> spend -> protect)
  business_day.py           # the AP-autopilot scripted day
  ingest/                   # invoice ingestion (vision extract -> governed decision)
  stripe_glue.py            # StripeGlue stub + LiveStripeGlue (real Connect Transfers)
  metrics.py                # run rollups
  web/server.py             # FastAPI: /run, /run_operator, /authorize, /state, /reset, /approve, /deny
  mcp_server.py             # Hermes MCP server exposing the governed door
  cli.py                    # demo runner
scenarios/                  # 10 JSON fixtures (the AP demo storyboard)
dashboard/                  # dashboard + phone approval UI (Alex's lane)
tests/                    # 16 test modules + conftest (136 passing clean)
docs/                       # plans, diagrams, specs, handoffs
integration/                # ready-to-merge Hermes mcp_servers.yaml fragment
```

### Web endpoints (`arbiter/web/server.py`)
- `POST /run_operator` — the headline demo (autonomous operator: earn → spend → refuse → escalate)
- `POST /run` — AP-autopilot scripted day
- `POST /authorize` — the agent/MCP submits a payment for governance (this is the MCP seam)
- `GET /state` — the dashboard poll feed (timeline, business rollup, payments, **reconciliation**)
- `POST /approve/{event_id}` · `POST /deny/{event_id}` — owner taps (id in URL path, not body)
- `POST /reset`

---

## 5. Sponsor rails (real vs mock)

| Sponsor | How it's used | Activates on | Without key |
|---|---|---|---|
| **Stripe** | `LiveStripeGlue` — real test-mode money via Connect Transfers (`tr_`) + confirmed PaymentIntents (`pi_`) | `STRIPE_SECRET_KEY=sk_test_…` | faithful no-money stub |
| **NVIDIA Nemotron** | bounded spend/decision judgement over NIM; OpenRouter fallback | `NVIDIA_API_KEY=nvapi-…` | MockNemotron |
| **Nous / Hermes** | MCP server exposes the governed `settle()` door | always (it's the seam) | n/a |

Boot banners print REAL vs stub/mock for each, so a recorded demo can truthfully claim real sponsor tech. **Proven live this session:** real PaymentIntent `pi_3Tma5cAUTWt2x6uq0ctw4PA2` retrieved from Stripe — status `succeeded`, £140, `livemode=false`.

**Key setup is fully documented** in `docs/KEY_DROP_FOR_ATLAS.md` (tracked, on `main`) — Stripe Connect one-time enable, the NVIDIA key-from-model-page gotcha, and the OpenRouter Nemotron fallback. Keys go into `/root/arbiter.env` machine-to-machine over SSH; **never paste key values into any chat channel.**

---

## 6. The Hermes/MCP angle (why this wins the "agent" requirement)

Arbiter registers as a **native Hermes MCP server** (`arbiter/mcp_server.py`). A Hermes agent handed a Stripe key *physically cannot* pay an unapproved payee, double-pay, overpay, or move money on a weak bank change without a human tap — because `mcp_arbiter_authorize_payment` is the **only** payment primitive it has, and that call fuses decide+execute. Skipping the gate doesn't skip a check; it skips the only way to pay anyone at all. Fails **closed** if the governance server is down. Full detail: `HERMES_INTEGRATION.md` (tracked). This is the hackathon's "Hermes Agent" requirement satisfied at the seam, not bolted on — and the prize bundles the winner's tool into Hermes as an MCP, which Arbiter already is.

---

## 7. Where the plan stands (your decision space)

Routed through an independent reviewer (atlas jr) → **SHIP-WITH-CHANGES**. Every catch was re-verified against live code before adoption — the full trail table is in `docs/FEATURE_EXPANSION_PLAN_V2.md` (v3, unpushed).

**Build order:** F1 ✓ → B0 ✓ → F4-lite + F5-lite ✓ → **F3 procurement scout (NEXT — the differentiator)** → F2 hash-chain (only if it stays tiny). Reviewer's strategic call: F2 was over-ranked ("we also have an audit ledger" only *neutralises* a competitor); the win is **"made a cheaper governed buy, then refused the bad one"** — agency plus restraint. F3 is that beat.

**F3 in one line (the safe version of Ben's web-search idea):** the model proposes a `{catalog_item_id}` only; the backend canonicalises price/category/vendor from an owner-curated catalog and builds the `SELF_SPEND` event from those canonical values, so a raw LLM never supplies a number that touches money. "Research advises, policy decides."

### Open decisions that need an owner

1. **Push `feat/f1-nemotron3-default`?** It's local-only. Nothing this session is reviewable on GitHub until it's pushed. *Helios will not push without Ben's explicit ok* — this is the first thing to resolve, because it gates whether you work via clone or SSH.
2. **F3 sourcing:** static seeded catalog (recommended, fully safe) vs whitelisted vendor API.
3. **F4-full:** provision a restricted `rk_` key + out-of-scope-rejection screenshot — only if the spine lands early.
4. **Stash `stash@{0}`** (`scrollApprovalIntoView` UI fix): Alex's lane or drop?

### One honest boundary (don't let it get lost)
`provision_capability` (the self-spend rail) is **still a stub even on the live glue** — inbound `pi_` ids are real, self-spend `stripe_id` is null. Documented in the B0 commit and the Alex `/state` contract; deliberately **not** overstated on the `/state` surface. If a judge inspects, it reads honestly.

---

## 8. Team lanes (so you don't reassign across them)

- **Ben** — policy engine, agent core, ledger, operator loop, reinvest, tests, demo runner; orchestration, narrative, submission, repo admin. Writes the code in paired-build mode.
- **Alex** — Stripe webhook/Connect layer, dashboard, phone approval UI, scenario generation. Current task list + full `/state` contract: `docs/handoffs/ALEX_TASKS_2026-06-26.md`.
- **Helios** (me) — execution: builds, fixes, tests, live-ops on the VPS with scoped read access. I implement once direction is set.
- **Taylor** — mentor, reviews.

---

## 9. First moves I'd suggest for you

1. **Resolve the push question with Ben** (decision #1) — it unblocks everything else and decides clone-vs-SSH for your whole workflow.
2. **Clone the baseline now** regardless — `git clone`, `pip install -e ".[dev,stripe]"`, `pytest`, `python -m arbiter.cli`. You'll understand the architecture in 20 minutes; it's 4.6k LOC and runs mocked.
3. **Read in this order:** `README.md` → `HERMES_INTEGRATION.md` → `arbiter/policy/rules.py` (the moat) → `arbiter/agent/agent.py` (`settle()`) → `docs/FEATURE_EXPANSION_PLAN_V2.md` (needs SSH or push).
4. **Decide F3 direction** — it's the build-defining call. I execute once you've set it.

---

*Verified against live repo state on helios-prod, 2026-06-26. If a fact here drifts from the tree, the tree wins — re-check with `git status` / `git log` on the box.*

---

## 10. Atlas's onboarding questions — resolved (2026-06-26)

Atlas reviewed this doc and raised five. Status of each:

**1. Can Helios push `feat/f1-nemotron3-default` now?** ✅ **DONE.** Pushed to origin after a secret scan (no key material in the diff or docs; no `arbiter.env`/`state/` tracked; repo confirmed **private**). The planning docs (this onboarding, the v3 plan, the procurement diagram, the hash-chain spec) were committed to the branch too, so it's reviewable end to end. Branch tip `5408724`, 4 commits ahead of `main`. `git fetch && git checkout feat/f1-nemotron3-default` to review.

**2. F3 = static seeded catalog?** ✅ **Agreed.** Static seeded catalog. Vendor API is too much surface this late; the seeded catalog is safer, demoable, and still proves governed procurement (model proposes a `catalog_item_id`, backend canonicalises price/category/vendor). This is now the F3 plan of record.

**3. Freeze scope after F3?** ✅ **Agreed.** Freeze after F3 + dashboard polish. F2 hash-chain only if it stays genuinely small (it's ~15 lines + 5 tests per spec; if it grows, it's cut). No "one more clever thing."

**4. Exact Stripe demo claim?** ✅ **Refined — and it's stronger than the first wording.** I verified against `arbiter/stripe_glue.py`. The precise, defensible line:

> "Real Stripe test-mode rails. Inbound client payments are **real confirmed PaymentIntents** (`pi_...`, verifiable in the Stripe dashboard). Supplier payouts use **real Connect Transfers** (`tr_...`). The agent never holds the Stripe key — every payment passes the governed `settle()` seam. The one exception is **self-spend/reinvest** (`provision_capability`), which is **recorded-only even live** — deliberately not wired to move money, for safety. Reconciliation proves ledger intent equals settled rail outflow."

Correction to Atlas's draft: it's not just "inbound real, outbound stubbed." **Outbound supplier transfers (`pay_supplier` → `tr_`) are real on the live glue too.** Only the **self-spend** path is recorded-only. Claiming outbound is fully stubbed would *undersell* us. Proven live: `pi_3Tma5cAUTWt2x6uq0ctw4PA2`, status `succeeded`, £140, `livemode=false`.

**5. Who owns the 60–90s narrative cut?** ⏳ **Ben's call.** Atlas proposes: Atlas owns narrative/orchestration, Helios execution, Alex frontend — one person holding the story so the demo stops being a feature inventory. That role split needs Ben's sign-off; flagging it, not deciding it.
