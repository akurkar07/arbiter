# Dashboard request: row-level Stripe settlement receipts

Status: fulfilled by backend merge `1c15098` on June 27, 2026.

The dashboard now binds to the live `/state` fields:

- `customer_payments[]` for inbound `pi_...` receipts.
- `supplier_payments[]` for outbound supplier `tr_...` receipts.
- `settlements[]` for row-level outflow receipts keyed by `event_id`.
- `reconciliation` for `{ledger_spend, rail_settled, drift, ok, failed_calls}`.

Historical request below kept for traceability.

The dashboard now consumes the Stripe fields already exposed on `/state`:

- `business.jobs[].payment_id` for inbound client `pi_...` receipts.
- `supplier_payments[]` for AP/vendor `tr_...` receipts keyed by `ref`.

One operator path is still ledger-backed rather than Stripe-backed in the UI:
approved `self_spend` delivery rows use `provision_capability()`, whose result is
not exposed on `/state` with a row id. To make the reconciliation strip literally
show `ledger spend == Stripe settled total` for every operator spend row, expose:

```json
{
  "settlements": [
    {
      "event_id": "northstar:spend:stock_footage",
      "kind": "self_spend",
      "amount": 60.0,
      "currency": "GBP",
      "stripe_id": "tr_...",
      "stripe_object": "transfer",
      "backend": "live-test"
    }
  ]
}
```

The dashboard can then:

- Link every paid row by matching `settlements[].event_id` to `timeline[].id`.
- Compute the Stripe settled-out total as the sum of `settlements[].amount`.
- Flag drift when `state.spend !== sum(settlements[].amount)`.

Until this exists, the strip reconciles `state.spend` against
`business.cost_spent`, and labels the outflow as `ledger-backed` when no real
transfer ids are present.
