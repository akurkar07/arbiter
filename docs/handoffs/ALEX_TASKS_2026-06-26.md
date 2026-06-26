# Alex — task list (run-up to submission, June 30)

Context: the engine side is done and live — `/run_operator` runs the real margin
engine with live Nemotron + Stripe test-mode, and the timeline now stamps `job` +
`margin_killer` on every row so your hero treatment and per-job grouping fire on
real data (PR #4). Atlas's competitive brief says the field is crowded with
"autonomous business agent" demos, and our one differentiator is the **margin
refusal beat** — "it refused to buy that because it would have made the job
unprofitable." Your lane is what makes that beat impossible to miss. Everything
below is dashboard / Stripe-surface / demo work; none of it touches the governance
core.

Each task has a **done-when** so you know when to stop. Rough order of impact.

---

## P0 — make the margin story unmissable (this is the whole pitch)

### 1. Put NVIDIA Nemotron on the climax frame
The SPEND ENGINE pipeline counter currently reads `Nemotron: 0` even though
Nemotron judges every single spend. It's counting the *deciding* layer (rules,
correctly) and never reading the advisory NIM judgement that's already in
`/state` under `business.jobs[].spends[].judgement`.

- Count Nemotron judgements from the business rollup, not the timeline layer:
  ```js
  const nemotronCount = (state.business?.jobs || [])
    .flatMap(j => j.spends || [])
    .filter(sp => String(sp.judgement?.source || "").startsWith("nim:"))
    .length;
  ```
  Set `el.pcModel.textContent = nemotronCount` instead of the current `t.model`.
- On the margin-killer hero row, surface the NIM `reason` string (the model's own
  words: "would exceed the margin-safe budget, making the job unprofitable") next
  to or instead of the rules reason. That sentence is the NVIDIA sponsor beat.
- **Done when:** a live `/run_operator` run shows a non-zero Nemotron count and the
  hero row displays the model's own refusal sentence. Full detail + line numbers in
  `docs/handoffs/ALEX_NEMOTRON_ON_CLIMAX_2026-06-26.md`.

### 2. A headline banner that states the outcome in words
Right now the numbers are there but a judge has to read the ledger to get the
story. Add a one-line verdict banner that updates after a run:
- Something like: **"Protected £285 of margin · refused £45 of unprofitable spend ·
  0 bad payments."** Pull from `state.business` (`net_profit`, `waste_blocked`,
  margin figures already exposed).
- Make the refused-spend figure visually distinct (the thing competitors can't
  claim).
- **Done when:** the banner renders the protected-margin + refused-spend line from
  live state, no hardcoded numbers.

### 3. The refusal beat needs to feel non-obvious
Atlas's brief is explicit: fraud blocking is *expected* and every competitor has
it. Our memorable beat is a **legitimate-sounding** purchase refused because the
margin would go negative — not fraud. The current hero spend (premium stock library
£45 on a £15-margin-left job) is right; make sure the UI frames it as "a reasonable
tool, refused on economics," not lumped in with the fraud blocks.
- Visually separate "refused — would kill margin" from "blocked — fraud/policy."
  Different label, different color. They are different stories.
- **Done when:** the margin refusal is visually distinct from the fraud blocks in
  both the event feed and the per-job ledger.

---

## P1 — sponsor-proof surfaces

### 4. Stripe receipts visible in the UI
We move real test-mode money (Connect Transfers `tr_...`, PaymentIntents `pi_...`).
A judge who sees the Stripe id in the dashboard *and* can find it in the Stripe test
dashboard is the strongest possible proof.
- Surface the `tr_`/`pi_` id on each paid row (it's in the ledger entry / settlement
  result). Link out to the Stripe test dashboard object if you can.
- **Done when:** a paid row shows its real Stripe id from a live run.

### 5. Reconciliation strip (close the loop)
After a run, show decision → payment → settlement as one chain: ledger spend total
== sum of Stripe transfer amounts. This is what makes it read as a *system*, not a
script. The backend can expose the totals — if you need a `/reconcile` endpoint or
a field on `/state`, write down exactly what shape you want and I'll wire it.
- **Done when:** a reconciliation line shows ledger-total == Stripe-total for the
  run (and flags any drift).

---

## P2 — demo polish (only if P0+P1 land)

### 6. Phone escalation UI
The owner-approval tap is currently a button in the dashboard. If there's time, a
real phone push (Twilio SMS with a tap-to-approve link, or a phone-framed mobile
view) makes the "human is the final layer" story land harder. The backend already
parks on `awaiting_approval` with `approve_url`/`deny_url` — you just need the
surface.
- **Done when:** the escalation can be approved/denied from something that looks
  like a phone, not just a desktop button.

### 7. 60–90 second demo cut
Atlas's brief asks for a demo narrative that lands without explanation. Once the
above is in, record the operator run: invoice in → spend approved → **the refusal**
→ phone escalation → protected-margin ledger. The refusal is the climax; everything
builds to it.
- **Done when:** there's a <90s clip that tells the story with no voiceover needed
  to understand it.

---

## What NOT to touch
- `arbiter/policy/`, `arbiter/agent/`, `arbiter/operator.py`, `arbiter/ledger/` —
  governance core, done and tested. If you need a new field exposed, ask and I'll
  add it to `/state` rather than you reaching into the core.
- The `settle()` single-money-door contract — the agent holds no Stripe key by
  design; don't add a payment path that bypasses it.

## If you need a backend change
Don't hack around a missing field. Write the exact JSON shape you want on `/state`
(or the endpoint you need) into a note and hand it over — backend publishes, you
bind. That keeps your diffs in the dashboard and avoids merge pain in the core.
