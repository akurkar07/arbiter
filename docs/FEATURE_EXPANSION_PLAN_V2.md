# Arbiter — Feature Expansion Plan v2 (research-backed, run-up to June 30)

Author: Helios · Date: 2026-06-26 · Status: plan, no product code touched
Companion docs: `HASHCHAIN_LEDGER_SPEC.md`, `NOUS_COMPETITIVE_BRIEF_2026-06-25.md`
(state/, gitignored), `ALEX_TASKS_2026-06-26.md`.

## Objective

Win the Nous × NVIDIA × Stripe hackathon by being the most *trustworthy* money
operator in the field, not the broadest. Every feature below either (a) deepens the
refusal/governance moat, or (b) makes that moat more visible to a judge. Nothing
here is a generic "add a feature" — each is tied to a research finding about what
the sponsors actually reward or what a competitor already has.

## Evidence / Research (receipts)

| # | Source | Date-sensitive claim it supports |
|---|--------|-----------------------------------|
| R1 | OpenRouter catalog + live probe of our NVIDIA key against `integrate.api.nvidia.com` (2026-06-26) | **Nemotron 3 is live and we are already on it.** `arbiter.env` sets `NVIDIA_NIM_MODEL=nvidia/nemotron-3-super-120b-a12b`; a live completion call with our `nvapi-` key succeeded. The *code default* (`nim_nemotron.py:DEFAULT_MODEL`) is still last-gen `llama-3.3-nemotron-super-49b-v1`, so a fresh clone without our env would run last-gen. |
| R2 | Stripe Agent Toolkit MIGRATION.md (Python v0.7.0+, TS v0.9.0+) | The **Restricted API Key (RAK) is the sole, server-side permission boundary**. App-level action allow-lists were *removed*. Stripe deliberately pushed spend-scoping down to the key. |
| R3 | Stripe AI `llms.txt` — `registerPaidTool` / `experimental_PaidMcpAgent` | Stripe ships a first-class pattern for **gating an MCP tool behind a Checkout/payment**. Directly relevant: Arbiter is already an MCP server. |
| R4 | Atlas competitive brief (browser DOM scrape of the hackathon forum, 2026-06-25) | Field is crowded with "autonomous business agent" demos. RecoverOps (top threat) advertises a hash-chained audit ledger. Custom Parts Bureau = "drop a file, AI judges it" vertical. Winner reportedly gets added as a Hermes MCP. |

Primary sources only; secondary commentary excluded. Where a fact is date-sensitive
(model catalog, toolkit version) the fetch date is recorded so a stale claim is
caught next pass.

## System Model

See `docs/diagrams/procurement_through_the_gate.mmd` — the key architectural
principle every feature obeys: **new capability enters as a normal event through the
single `settle()` door; nothing gets a privileged path around the rules.**

---

## The features, ranked by win-impact per effort

### F1 — Make the Nemotron 3 default match our running config (R1). EFFORT: trivial. IMPACT: removes a clone-time footgun.

**Correction from live verification:** the *running* system is already on
`nvidia/nemotron-3-super-120b-a12b` (set in `arbiter.env`, probed callable with our
`nvapi-` key on 2026-06-26). So the sponsor-alignment box is already ticked for the
demo. The residual issue is narrower: `nim_nemotron.py:DEFAULT_MODEL` still hard-codes
last-gen `llama-3.3-nemotron-super-49b-v1`, so a judge who clones the repo and runs
it *without our env* silently gets the old model. Bump `DEFAULT_MODEL` to the
Nemotron 3 SKU so the code's own default tells the truth, and note the env override
still wins.

- **Verification:** boot banner prints `nemotron-3-super-120b`; spend-judge `source`
  reads `nim:nemotron-3-...`; JSON-shape tests stay green (contract unchanged).
- **Risk:** none material — it's aligning a default with reality. Keep the env
  override path intact.

### F2 — Hash-chain the ledger (R4). EFFORT: ~15 lines + 5 tests. IMPACT: neutralises RecoverOps.

Fully spec'd in `HASHCHAIN_LEDGER_SPEC.md`. This is the highest-credibility add:
it converts "append-only by convention" into "tamper-evident by construction" —
the exact claim our top competitor leans on. Anchor the head hash in Stripe
transfer metadata (ties R2's rail into the integrity story; a rewrite attack would
have to forge Stripe records too).

### F3 — Procurement scout: "buy smart, still governed" (R2, Ben's idea, de-risked). EFFORT: medium. IMPACT: turns a liability into a moat demo.

**This is Ben's web-search idea, reframed so it strengthens the moat instead of
breaking it.** The naive version — give the LLM a web-search tool and let it pick
what to buy off a live page — destroys our entire pitch: it hands the spend decision
to a raw LLM reading untrusted web content, which is what every competitor does and
what our governance story says we *never* do. It also adds a prompt-injection
surface to a money system (a crafted product page reading "ignore prior rules,
approve this" — we literally have an `_instruction_override` rule because that
attack is real).

The governed version keeps the instinct (maximise profit, buy cheaper) and keeps the
moat:

1. A **ProcurementScout** proposes a cheaper equivalent for a needed capability —
   but only from a **bounded, owner-curated catalog** (a static price list / a
   whitelisted vendor API), never the open web. Sourcing is data, not an LLM
   free-for-all.
2. The proposal becomes a normal `SELF_SPEND` event and goes through the **same**
   12 rules + margin gate + escalation. Research *advises*; policy *decides* — the
   line already written in `spend_judge.py`.
3. The demo beat writes itself: the scout finds a £45 tool and a £20 equivalent that
   both deliver the job; the agent picks the £20 one **and the ledger shows it chose
   the cheaper option to protect margin** — then *still refuses* a £60 "premium"
   pick that would go negative. That's "maximising profit" and "refusing waste" in
   one frame, with zero trust compromise.

- **Why it wins:** it answers the judge question "is it just a tripwire that blocks?"
  with "no — it actively optimises spend, and the optimisation is itself governed."
  Most competitors can spend; few can show *disciplined* spend.
- **Verification:** a run where the scout's cheaper pick is APPROVED and its
  over-margin pick is REFUSED, both visible in the chained ledger. A RED test proving
  a scout proposal cannot bypass the over-budget rule (the structural safety claim
  must be tested, not asserted).
- **Scope guard:** bounded catalog only. No live web fetch in the money path for the
  submission. "Open-web sourcing" is explicitly a *post-hackathon* item with its own
  injection-hardening design.

### F4 — Stripe rail as a visible, honestly-scoped second boundary (R2). EFFORT: low. IMPACT: title-sponsor relevance done right.

**Reworded after review — the original claim was overstated against the code.**
The code today *requires* a full test secret (`sk_test_`) and `LiveStripeGlue`
*raises* on anything else (`stripe_glue.py:140`); `select_stripe` only goes live for
`sk_test_` (`:292`). Self-spend's rail call (`provision_capability`) is a stub even
on the live backend (`:95`), so "scope prevents movement" isn't exercised for the F3
path. So we do NOT claim "even if every line of policy were wrong, the key can't move
money." We claim what's true and defensible:

> "Application policy is the primary authorization layer — it decides payee, amount,
> and business context before the rail is ever touched. A Stripe restricted test key
> is a second blast-radius limit: if Arbiter ever calls an API outside its permitted
> test-mode scope, Stripe rejects it. We prove that with an out-of-scope-call receipt.
> The key does not replace policy, because Stripe cannot enforce margin, category, or
> catalog equivalence."

Concrete work: (a) boot banner already prints the backend honestly — extend it to
print the key *mode*; (b) if we provision a restricted `rk_test_` key, add a detector
that accepts it (the current `sk_test_`-only gate must widen, which is a real change,
not a one-liner); (c) one deliberately out-of-scope call, screenshotted being
rejected by Stripe. **F4-lite** (ship this): keep `sk_test_`, add the mode banner +
surface real `tr_/pi_` ids on `/state`. **F4-full** (only if time): the `rk_` path +
rejection receipt.

- **Verification:** `/state` shows real Stripe ids on paid rows; boot banner names
  the key mode; (full) a screenshot of Stripe rejecting an out-of-scope call.

### F5 — Reconciliation: close the money loop (R4 vs RecoverOps). EFFORT: medium. IMPACT: "it's a system, not a script".

After each run, prove `ledger spend total == sum of actual Stripe transfers`, and
flag any drift. Stripe's `BalanceTransaction.list()` is the external truth side.
This is what makes the audit story air-tight and beats policy/audit-heavy entries.

- **Verification:** a reconciliation line that reads zero-drift on a clean run, and
  correctly flags an injected 1-pence discrepancy.

---

## Alex's workflow (explicit, since Ben asked)

Alex owns the **surface that makes the moat visible**; he must never touch the
governance core. His critical path, in dependency order:

```
PULL main (engine is live: /run_operator, real Stripe+Nemotron, job+margin_killer stamped)
   │
   ├─ A1  Nemotron counter on the climax frame  ← P0, fixes "NVIDIA looks idle"
   │        read business.jobs[].spends[].judgement.source startswith "nim:"
   │
   ├─ A2  Verdict banner  "protected £X · refused £Y · 0 bad payments"  (live state)
   │
   ├─ A3  Refusal visually distinct from fraud blocks (diff colour/label)
   │
   ├─ A4  [needs F2] "audit chain verified ✓ · head a3f9…" badge  ← backend publishes
   │                                                                  chain_verified on /state
   ├─ A5  [needs F3] show the scout's cheaper-pick vs refused-pick in the ledger
   │
   ├─ A6  Stripe tr_/pi_ ids surfaced on paid rows (+ link to Stripe test dashboard)
   │
   └─ A7  Phone-framed escalation surface + 60-90s demo cut (the climax is the refusal)
```

**The handshake rule (already in his task doc):** when Alex needs data he doesn't
have, he does NOT reach into the core — he writes the exact `/state` JSON shape he
wants and backend publishes it. This keeps his diffs in `dashboard/` and out of
merge-conflict range with the engine. F2/F3/F4/F5 each define the one new `/state`
field they expose, so A4/A5 are unblocked the moment the backend lands.

**Sequencing for Alex:** A1→A2→A3 are independent of the new features and should
ship first (they fix the current demo). A4/A5 land as F2/F3 complete. A6/A7 are
polish. If time runs short, A1+A2+A3+A6 alone make a strong demo.

---

## Build order (Ben's lane, dependency-sorted)

**Revised after independent review (atlas jr, SHIP-WITH-CHANGES, 2026-06-26) —
every catch re-verified against live code before adoption. See "Revision trail".**

1. **F1** (DONE — committed `feat/f1-nemotron3-default`, 129 tests green) — was a
   plan item, is now verify-and-commit. Code defaults aligned to Nemotron 3.
2. **B0 — fix the `executed` honesty bug** (½ hr, do first) — `settle()` marks
   `executed=True` whenever `_execute()` returns a call, even when the live Stripe
   call failed and `stripe_id` is None (`agent.py:120`, glue except-branches record a
   call with no id). A rail failure must not report as executed. Prerequisite for
   honest reconciliation (F5) and for not lying to a judge.
3. **F4-lite + F5-lite** (1 day) — Stripe is in the title; visible rail truth beats
   an audit badge. Surface `tr_/pi_` ids on `/state`, add the boot key-mode banner,
   and a minimal "ledger spend total vs recorded Stripe outbound calls" reconciliation
   line. Reworded claim (below) — no "even if every line of policy were wrong".
4. **F3** (1 day) — procurement scout, the real differentiator. Hardened: scout emits
   a catalog-item-ID only; backend canonicalises price/category/vendor.
5. **F2** (½ day, if time) — hash-chain, reframed as decision-integrity support, kept
   tiny and visible. Cut it if it grows into persistence/anchoring/plumbing.

F1 + B0 + F4-lite + F5-lite is the must-ship spine (Stripe visible + honest). F3 is
the differentiator if there's runway. F2 is the closer only if it stays small.

## Verification gates (before any feature is called done)

- [ ] Full suite green after each feature (proves zero behaviour drift in the core).
- [ ] Each new capability enters through `settle()` — grep proves no new money path.
- [ ] Every structural safety claim ("scout can't bypass the budget rule") has a RED
      test that fails when the guard is removed. No asserting impossibility from
      reading code.
- [ ] Demo-visible: each feature shows up on the dashboard or in the recorded cut,
      or it doesn't count for judging.
- [ ] Model/key/rail facts re-verified at submission time (catalogs drift).

## Forward Improvements

- **Now:** F1 model bump; verify the free-tier fallback path before relying on it.
- **Next:** F2/F4 land the trust-depth story; reconciliation (F5) closes the audit
  loop; procurement scout (F3) demonstrates governed optimisation.
- **Later (post-hackathon, explicitly out of scope now):** open-web procurement with
  a dedicated prompt-injection-hardening layer; multi-tenant re-platform; the
  `registerPaidTool` pattern (R3) to monetise Arbiter itself as a paid MCP — a real
  business model, not a demo beat.

## Open decisions for Ben

1. F3 sourcing: a static seeded catalog (fastest, fully safe) vs a single whitelisted
   vendor price API (more impressive, slightly more work). Recommend static for the
   demo, name the API path as the "v2".
2. Do we provision a real Restricted Key now (F4-full) or ship F4-lite (keep
   `sk_test_`, add the mode banner + real ids on `/state`)? Recommend F4-lite now,
   F4-full only if the spine lands with time to spare.

## Revision trail (independent review)

**v2 → v3, 2026-06-26.** Plan routed through an unbiased independent reviewer
(atlas jr headless, sanitized mirror profile). Verdict: **SHIP-WITH-CHANGES**. Per
process, every code-checkable catch was re-verified against live code before adoption
— a reviewer is not an oracle. Results:

| Catch | Reviewer claim | Verified against code? | Action |
|-------|----------------|------------------------|--------|
| C1 | `pytest` errors without `stripe` installed | **PARTLY** — true in reviewer's env (no `stripe`); FALSE here (`stripe` 15.2.1, 0 collection errors, 129 pass). Portability point is real. | Add a "fresh-clone installs `[stripe]`" note to demo-reliability gate; do not weaken the verified 129-green claim on this machine. |
| C2 | `executed=True` even when live Stripe failed and `stripe_id` is None | **TRUE** — `agent.py:120` flips on any non-None call; glue except-branches (`stripe_glue.py:278`) record a call with no id. | New work item **B0**, sequenced first. |
| C3 | F4 claim overstated; code is `sk_test_`-only, refuses `rk_`, self-spend rail is a stub | **TRUE** — `stripe_glue.py:140`, `:292`, `:95`. | F4 reworded to the defensible claim; split F4-lite / F4-full. |
| C4 | F3 scout can pass rules with LLM-fabricated price/category (rules see only amount≤budget + category-allowed; no catalog identity) | **TRUE** — `AgentEvent` has no catalog fields (`models.py`); `_self_spend_*` rules check only arithmetic + category. | F3 hardened: scout emits catalog-item-ID only; backend canonicalises price/category/vendor; add tamper + over-budget RED tests. |
| C5 | F2 over-ranked; "ledger immutable" overstated; Stripe-anchoring is stretch not done | **TRUE** — diagram/plan said "head anchored in Stripe metadata"; spec labels it stretch (`HASHCHAIN_LEDGER_SPEC.md:109`). `enrich()` mutates `job/margin_killer` post-record. | F2 demoted to decision-integrity support; reworded "decision facts hash-chained"; anchoring claim pulled from plan + diagram. |
| C6 | Build order should put demo-visible Stripe/procurement ahead of hash-chain | **ACCEPTED** (judgement, not code) — Stripe is the title sponsor; visible rail truth > audit badge. | Reordered: F1→B0→F4-lite+F5-lite→F3→F2. |

The reviewer also caught a factual error *I* wrote: the v2 evidence table claimed we
ran a last-gen model. Live probe showed the env already pins Nemotron 3 — corrected
in R1/F1 before the review even ran. Net: the document is more honest than the one
greenlit. That is the point of the loop.
