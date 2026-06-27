# Dashboard request: audit-chain badge state

`docs/FEATURE_EXPANSION_PLAN_V2.md` lists Alex task A4:

> "audit chain verified ✓ · head a3f9…" badge

That badge is intentionally blocked until the backend publishes the decision-chain
state. Alex should not implement hash-chain logic in the dashboard or reach into
`arbiter/ledger/`.

Requested `/state` shape:

```json
{
  "chain_verified": true,
  "ledger_head_hash": "a3f9c2e4...",
  "ledger_entries_hashed": 12
}
```

Dashboard binding once available:

- Green compact badge near the live event feed or reconciliation strip:
  `Audit chain verified · head a3f9c2e4`
- Red badge if `chain_verified === false`.
- Hide the badge when the fields are absent, so current runs do not imply a
  hash-chain guarantee that the backend has not exposed yet.
