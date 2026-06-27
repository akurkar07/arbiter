# Handoff to Alex — surface NVIDIA Nemotron on the live climax frame

**Date:** 2026-06-26 · **From:** engine lane · **Status:** one UI gap, data is ready

## What changed on the engine side (already done, 129 tests green)

1. **The "Go live" button now plays the real margin engine.** `dashboard/app.js`
   `startLive()` was POSTing `/run` (the old AP-autopilot day — no margin
   refusal). It now POSTs `/run_operator`, the autonomous money-operator that
   earns, verifies, and refuses margin-killing spend. This is the run your UI
   was already built to render. (One-line swap in your file — flagging it here
   so you see it; revert if you'd rather wire a separate button.)

2. **Live timeline rows now carry `job` + `margin_killer`.** The operator stamps
   them via `EventLedger.enrich()`. Your `isHero()` (app.js:164, reads
   `row.margin_killer === true`) and per-job grouping (app.js:309, reads
   `r.job`) now fire on **live** data, not just `sample_state.json`. Confirmed by
   vision: the premium_stock_library refusal renders as the red hero row and the
   per-job ledger shows "£45.00 refused to protect margin".

3. **The NIM narrative on the climax beat is fixed.** `nemotron-3-super-120b` is
   a reasoning model; it spent 150–310 tokens thinking before the JSON, and the
   512 `max_tokens` cap was truncating the answer to `malformed` intermittently —
   landing the fail-safe on the most important frame. Raised to 1024 + added a
   `reasoning_content` fallback. 6/6 clean real-Nemotron narratives now.

## The one gap left — your lane (presentation only, data is already in /state)

**Symptom (vision-verified):** the SPEND ENGINE pipeline counter reads
`Layer 1 (rules): 7 · Layer 2 (NVIDIA Nemotron): 0 · Layer 3 (owner): 0`.
NVIDIA looks idle on the climax frame even though it judged **every** spend.

**Why:** `tallyFrom()` (app.js:354) counts `stageOf(row.layer)`, and `row.layer`
is the *deciding* layer. A margin block is correctly decided by `rules`, so it
counts as rules. The Nemotron spend-judgement is **advisory** — it lives in
`business.jobs[].spends[].judgement`, which the counter never reads. So real
NVIDIA work is invisible.

**The data you need is already in `/state`** (no engine change required). This
run exposed 3 real judgements:

```
business.jobs[].spends[].judgement = {
  "source": "nim:nvidia/nemotron-3-super-120b-a12b",
  "reason": "Buying premium_stock_library would exceed the margin-safe budget, making the job unprofitable.",
  "decision": "block", "margin_ok": false, "on_goal": true, "risk_score": ...
}
```

**Two suggested fixes (your call):**

1. **Pipeline counter:** count Nemotron judgements from the business rollup, not
   the timeline layer. Something like:
   ```js
   const nemotronCount = (state.business?.jobs || [])
     .flatMap(j => j.spends || [])
     .filter(sp => String(sp.judgement?.source || "").startsWith("nim:"))
     .length;
   ```
   Set `el.pcModel.textContent = nemotronCount` (3 this run) instead of
   `t.model` (0). The honest framing: rules **decide**, Nemotron **judges every
   spend** — show both.

2. **Hero row narrative:** on the margin-killer row, surface the NIM `reason`
   string so the model's own words ("would make the job unprofitable") sit on
   the climax frame. Right now the hero row shows the rules reason; the Nemotron
   prose is the more visceral one and it's the NVIDIA sponsor beat.

## Receipts
- Live run state: `state/receipts/live_operator_button_wired_2026-06-26.json`
- Vision-verified frame: `state/receipts/dashboard_review/live_operator_hero_2026-06-26.png`
- Backend on this run: `stripe_backend: live-test`, real Nemotron 120b, balance
  £50→£215, waste_blocked £45, all margins protected.

Engine + ledger + enrichment are done and green. The story now lands on the one
button a judge clicks. This counter+narrative tweak is the last polish to put
NVIDIA on the climax frame.
