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

## UPDATE 2026-06-26 (read first) — P1 is now backend-unblocked

Since this list was written I shipped the backend for tasks **4 (Stripe receipts)**
and **5 (reconciliation)**. The fields you'd have had to ask for are live on `/state`
now — see the **`/state` contract** appendix at the bottom for exact shapes. Net:

- **Real Stripe ids are in the feed.** `customer_payments[]` carries inbound `pi_`
  ids (live-confirmed: `pi_3Tma5c…` retrieved from Stripe, `succeeded`, £140,
  test-mode). `supplier_payments[]` carries outbound `tr_` ids when a transfer lands.
- **Every paid row now has a `failed` flag** — render a failed rail call as failed,
  don't hide it. A judge trusts a dashboard that shows the one that broke.
- **Reconciliation is a ready-made block** — `reconciliation: {ledger_spend,
  rail_settled, drift, ok, failed_calls}`. Task 5's "ledger-total == Stripe-total,
  flag drift" is a direct bind now, no maths in the UI.

Priority order is unchanged: **P0 (Nemotron on the climax + verdict banner + refusal
made distinct) is still the whole pitch.** P1 is just cheaper than it was. One honest
caveat to render correctly: self-spend (`provision_capability`) is still a recorded
stub even on the live rail, so its `stripe_id` is null while inbound `pi_` ids are
real. Don't label a null-id self-spend as "live-settled" — see the contract note.

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

### 4. Stripe receipts visible in the UI  ✅ backend ready (2026-06-26)
We move real test-mode money (Connect Transfers `tr_...`, PaymentIntents `pi_...`).
A judge who sees the Stripe id in the dashboard *and* can find it in the Stripe test
dashboard is the strongest possible proof.
- The ids are on `/state` now: `customer_payments[].stripe_id` (inbound `pi_`) and
  `supplier_payments[].stripe_id` (outbound `tr_`). Both arrays also carry `failed`.
- Link out to `https://dashboard.stripe.com/test/payments/{pi_id}` for inbound and
  `…/test/connect/transfers/{tr_id}` for outbound if you want the click-through.
- Render a `failed: true` row as a visible failure, not a success with a blank id.
- **Done when:** a paid row shows its real Stripe id from a live run, and a failed
  rail call (if any) shows as failed rather than vanishing.

### 5. Reconciliation strip (close the loop)  ✅ backend ready (2026-06-26)
After a run, show decision → payment → settlement as one chain. The backend now does
the maths — `/state.reconciliation` gives you `{ledger_spend, rail_settled, drift,
ok, failed_calls}` directly.
- Bind the strip to those fields: "Ledger approved £X · Rail settled £Y · drift £Z".
  Green when `ok: true`, red when `false` (drift over a penny, or any `failed_calls`).
- `failed_calls[]` is the actionable list — approved in governance but the rail never
  settled. Show it when non-empty; it's the honest "something broke here" surface.
- **Done when:** a reconciliation line shows ledger-total vs rail-total for the run,
  goes green on a clean run, and surfaces any drift/failed call rather than hiding it.

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

---

## Appendix — `/state` contract (captured 2026-06-26, real shapes)

Poll `GET /state`. Top-level keys you bind to, with the new payment + reconciliation
surfaces called out. Field values below are real, from an actual run.

**Payment surfaces (new):**
```jsonc
"stripe_backend": "live-test",          // or "stub" — already drove your backend pill
"supplier_payments": [                  // outbound (money the operator paid out)
  { "payee": "aws", "amount": 220.0, "currency": "GBP",
    "stripe_id": null,                  // real "tr_..." on a live transfer; null on stub/self-spend
    "ref": "AWS-06", "failed": false }  // failed:true => rail errored, show as failed
],
"customer_payments": [                  // inbound (clients paying invoices) — these go LIVE
  { "ref": "inv_1001", "amount": 140.0, "currency": "GBP",
    "stripe_id": "pi_3Tma5cAUTWt2x6uq0ctw4PA2",  // real, retrievable in Stripe test dash
    "failed": false }
],
"reconciliation": {                     // F5-lite — bind the recon strip straight to this
  "ledger_spend": 85.0,                 // total the ledger approved to spend
  "rail_settled": 85.0,                 // total that actually settled on the rail
  "drift": 0.0,                         // abs(ledger_spend - rail_settled)
  "ok": true,                           // true => drift<=£0.005 AND no failed calls
  "failed_calls": []                    // [{op,payee,category,amount,currency,notes}] when broken
}
```

**Reading `stripe_id` honestly (matters for the receipts task):**
- `customer_payments[].stripe_id` → real `pi_` ids on a live run. Link + show.
- `supplier_payments[].stripe_id` → real `tr_` id when a Connect transfer lands;
  `null` on the stub.
- **Self-spend** (the reinvest beat) runs through `provision_capability`, which is a
  recorded stub even on the live rail, so its `stripe_id` stays `null`. Don't badge a
  null-id row as "settled on Stripe" — badge it "recorded" / "test-mode". This is the
  one place an over-claim would be caught by a judge, so render it precisely.

**Timeline row** (`timeline[]`, the event feed — unchanged, still your main bind):
```jsonc
{ "t": 1782485296.48, "id": "01_revenue_in", "kind": "invoice_payment",
  "decision": "approve", "reason": "Normal invoice payment reconciled …",
  "refs": ["invoice_normal_paid"], "risk": 0.05, "layer": "rules",
  "amount": 480.0, "currency": "GBP", "category": null,
  "beat": "Revenue in: Brightwave pays their £480 invoice …",
  "job": null, "margin_killer": false }   // job + margin_killer drive your grouping/hero
```

**Escalation card** (`awaiting_approval`, `null` until the run parks on the owner tap):
when present it carries the `event_id` you POST to `/approve/{event_id}` or
`/deny/{event_id}` (id in the URL path, not the body).

**Nemotron count** (P0 task 1): not a top-level field — derive it from
`business.jobs[].spends[].judgement.source` starting with `"nim:"`, per the snippet in
task 1. `business` is `null` until `/run_operator` starts.
