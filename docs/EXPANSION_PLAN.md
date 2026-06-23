# Arbiter — Expansion Plan (6 days to submission)

**Date:** 2026-06-23 · **Deadline:** 2026-06-30 · **Team:** Ben + Alex (`akurkar07`)
**Branch:** `feat/autonomous-operator-loop` (pushed, at `67c9a51`)

## Objective

The standalone demo works and the story is right (AP-autopilot: pay approved
suppliers, block the rest, escalate the ambiguous). With ~6 days and strong AI
agents, the goal is to move from *"a polished demo with mocked sponsor tech"* to
*"a system that provably runs on all three sponsors' real rails, doing a job a
business would actually pay for."* That is the gap that wins a sponsor-judged
hackathon — not more features on top of mocks.

## The blunt diagnosis (what "better" means here)

The submission's weakness is **not** missing features. It's that the two headline
sponsor integrations are still mocked, and a sponsor judge will check:

| Sponsor axis | Status today | Why it's stuck | The fix research found |
|---|---|---|---|
| **Hermes-native (Nous)** | ✅ Real | — | Done: MCP server, single-door settle, governed result |
| **NVIDIA Nemotron** | ⚠️ Code real, key 403s | Personal NGC key has no inference entitlement | Regenerate key from build.nvidia.com **model page** (not NGC). Fallback: OpenRouter free Nemotron. |
| **Stripe** | ❌ Pay path can't run | `pay_supplier` uses v2 OutboundPayments → needs a Treasury financial account (`fa_...`) we can't provision in test mode | Switch to **Connect Transfers** (`tr_...`) — real money to a supplier, 2-min free Connect enable, zero approval |

Both blockers have a concrete, researched way through. Fixing them is worth more
than any new feature, because they convert three "we integrated X" claims from
*asserted* to *provable in the sponsor's own dashboard*.

## Evidence / Research (receipts)

- **Stripe pay-path blocker.** `stripe_glue.py:139-164` calls
  `v2.money_management.outbound_payments.create(...)` which hard-requires
  `from.financial_account` (a Treasury `fa_...`). Treasury is a separate,
  eligibility-gated product — **not provisionable with a bare `sk_test_` key**.
  Source: Stripe API docs (v2 money-management / outbound-payments), confirmed
  against installed `stripe` SDK 15.2.1.
- **Stripe fix — Connect Transfers.** `stripe.Transfer.create(amount, currency,
  destination=acct_...)` moves real test-mode money to a connected "supplier"
  account (`tr_...`, visible under Balances → Transactions). Requires only a
  one-time, free, instant **Connect → Get started** in the dashboard. This is the
  closest zero-approval primitive to "agent pays a third-party supplier."
  Source: Stripe Connect docs (Account.create + Transfer.create).
- **Stripe zero-setup tier.** Customer (`cus_`) + Invoice (`in_`) + Checkout
  (`cs_test_`) + PaymentIntent (`pi_`) all work with a bare `sk_test_` key and
  produce real dashboard objects — usable for the revenue-in / invoice side even
  without Connect.
- **NVIDIA 403 root cause.** A key that authenticates on `/v1/models` but 403s on
  `/chat/completions` is a personal NGC key without inference entitlement. Fix:
  generate an **API-catalog key from the model page** on build.nvidia.com
  (`nvapi-...`). Source: NVIDIA NIM access docs + known 403 pattern.
- **NVIDIA fallback.** OpenRouter (`https://openrouter.ai/api/v1`) hosts free
  Nemotron variants (`nvidia/...:free`), OpenAI-compatible, supports
  `response_format: json_object` — the exact shape `nim_nemotron.py` already
  uses. Free tier ~20 req/day without credits; enough for a demo. Our
  `NimNemotron` client works against it by swapping `base_url` + key.
- **Feature depth — what real AP products do.** Ramp (real-time spend controls),
  Bill.com (OCR invoice ingestion + tiered approval routing), Tipalti (supplier
  onboarding with KYC). Human-in-the-loop best practice in 2026 agent frameworks
  = interrupt-and-resume + approval queue (LangGraph Agent Inbox), policy-as-code,
  audit trail. Our 3-layer model already matches the canonical pattern; the gaps
  vs real products are **invoice ingestion** and **reconciliation**.

## System Model

See `docs/diagrams/expansion_architecture.mmd`. Green = real rails after this
plan; blue = new capability; the single `settle()` door stays the only path to
money.

## Plan — ranked by impressiveness-per-effort, proof-gated

Each item has a **proof gate**: a real artifact through the real integration
before it counts. No UI polish on top of an unproven seam.

### P0 — Make the mocked sponsors real (highest leverage, do first)

1. **Stripe: swap `pay_supplier` to Connect Transfers.**
   - Add `LiveStripeGlue.ensure_supplier_account(vendor_id)` → `Account.create`
     (cached per vendor) and rewrite `pay_supplier` to `Transfer.create`.
   - Keep the existing record-on-error fallback (a rail hiccup must never crash
     governance).
   - **Proof gate:** a `tr_test_...` object from a demo run, visible in the
     test dashboard, logged in the ledger with its id.
2. **NVIDIA: unblock Nemotron.** Regenerate the key from the model page; if it
   still 403s, point `NimNemotron` at OpenRouter free via env
   (`NVIDIA_NIM_BASE_URL` override + `:free` model id). Already 90% supported —
   needs a base-url env knob.
   - **Proof gate:** `python -m arbiter.agent.nim_nemotron` selftest exits 0 with
     a real model id and a bounded decision (not the `nim_unreachable` fallback).

### P1 — One feature that turns "capability" into "the AP job" (pick ONE)

3. **Invoice ingestion (OCR).** Drop a PDF/image invoice → LLM vision extracts
   `{vendor, amount, invoice_ref, due_date, line_items}` → feeds the existing
   engine. This is the single most "it actually does finance work" moment and the
   hero feature of Bill.com/Tipalti.
   - **Proof gate:** a real sample-invoice PDF processed end-to-end into a
     governed decision, on camera.

### P2 — Reconciliation close-the-loop (if P0+P1 land with time to spare)

4. **Reconciliation view.** `BalanceTransaction.list()` after a run, matched
   against the ledger, to show decision → payment → settlement as one chain.
   Mirrors Stripe's payout-reconciliation report; it's what makes it read as a
   *system* not a script.

### Explicitly OUT of scope (resist these)

- Issuing cards, v2 OutboundPayments/Treasury (blocked, not worth the time).
- A second new vertical feature before P0 is proven (mocks → real is the win).
- Rewriting Alex's dashboard surface (`app.js`, `sample_state.json`) — backend
  exposes fields; UI binding is his lane. New backend fields get documented for
  him, not bound by me.

## Verification

- `tr_test_...` and `cs_test_...` ids visible in the Stripe test dashboard from a
  live demo run.
- NIM selftest exit 0 with real model id.
- Full pytest suite green after each change (currently 85 passing).
- Reconciliation: ledger spend total == sum of Stripe transfer amounts for the
  run (the 1-cent-drift class of bug caught structurally).
- Boot banners show `REAL test-mode` / `REAL NVIDIA NIM` — the honesty discipline
  that lets the video truthfully claim real sponsor tech.

## Division of labour (Ben + Alex)

- **Backend rails + governance + proof gates:** Helios scaffolds, Ben writes/owns
  the assessed pieces; the integration seams (Stripe Transfer swap, NIM base-url
  knob) are standard glue, fair to co-build.
- **Dashboard / video / pitch surface:** Alex's lane. Backend publishes the new
  fields (`tr_` ids, reconciliation totals, extracted-invoice payload); Alex
  binds them.
- **Keys:** routed to the box via the 600 env-drop, never chat. `arbiter.env` on
  helios-prod only.

## Forward improvements

- **Now:** P0 — both sponsor rails real and proven. This is the submission's
  single biggest credibility jump.
- **Next:** P1 invoice OCR — the feature that makes it a product, not a demo.
- **Later:** reconciliation view + a short "trust model" section in the README
  (why a Hermes agent can hold a Stripe key safely) — the infra-for-the-framework
  pitch the judges (who build the framework) care about.
