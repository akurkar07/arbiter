# Spec — Tamper-Evident Ledger (hash chain)

Status: ready to build. Read-only planning doc — no product code touched.
Owner: Ben (core). Effort: ~15-20 lines + ~5 tests. One method, two fields.

## Why

RecoverOps — our closest competitor in the competitive brief — advertises a "hash-chained
audit ledger." We have an append-only Python list whose docstring *says* "entries
are never mutated or deleted" but nothing *enforces* it. When a judge asks "what
stops someone editing a decision after the fact?", today's honest answer is
"nothing — it's append-only by convention."

This converts that to **append-only by construction**: any edit, deletion,
insertion, or reorder of the audit trail becomes mathematically detectable. It is
the single highest-credibility addition available and it directly neutralises
RecoverOps's headline claim.

## Scope

Changes `arbiter/ledger/event_ledger.py` only. No rule, agent, operator, or Stripe
change. The dashboard gets one new read (a verified badge) — Alex's lane, published
on `/state`, not bound here.

## Data model — two new fields on `LedgerEntry`

```
prev_hash:  str   # entry_hash of the previous row ("0"*64 for the genesis row)
entry_hash: str   # sha256 over THIS row's integrity core + prev_hash
```

Both are computed at `record()` time and never change afterwards.

## The one real design decision: the integrity boundary

The hash must cover the **immutable decision facts**, NOT the display metadata that
`enrich()` stamps on later (`job`, `margin_killer`). If the hash covered those,
every `enrich()` call would break the chain — and that's the existing margin-killer
feature, so we'd be sealing the row before it's fully tagged.

Resolution: the integrity core is the decision, the display tags are explicitly
outside the seal.

Integrity core (hashed): `timestamp, event_id, event_kind, decision, reason,
policy_refs, risk_score, decided_by, amount, currency, category`

Outside the seal (not hashed, mutable by `enrich`): `job, margin_killer, demo_beat`

State this in the docstring and the README: "the audit chain protects the decision
record. Display grouping/spotlight tags are cosmetic and explicitly outside the
integrity boundary." That is the honest, senior framing — and it's a *strength*,
not a hole: a reviewer who asks "can enrich tamper with a decision?" gets a clean
"no, by construction enrich cannot reach a hashed field."

## Hash construction

```
def _hash_core(core: dict, prev_hash: str) -> str:
    payload = json.dumps(core, sort_keys=True, separators=(",", ":")) + prev_hash
    return hashlib.sha256(payload.encode()).hexdigest()
```

- `core` = the integrity-core fields above, pulled off the entry.
- `sort_keys=True` + fixed separators = canonical, stable serialization (same input
  always yields the same hash across machines / Python runs).
- Genesis: the first row's `prev_hash` is `GENESIS = "0" * 64`.
- Row N: `prev_hash = entries[N-1].entry_hash`; `entry_hash = _hash_core(core_N,
  prev_hash_N)`.

`record()` computes `prev_hash` from the current tail, builds the entry, computes
`entry_hash`, then appends. No other call path writes entries, so the chain stays
total-ordered and gapless.

## verify() contract

```
def verify(self) -> ChainCheck:
    """Walk the chain. Returns (ok, broken_at, reason).

    ok=True  -> every row's entry_hash recomputes correctly AND every row's
                prev_hash equals the previous row's entry_hash (genesis for row 0).
    ok=False -> broken_at is the index of the first bad row; reason names the
                failure (mutated row | broken link | bad genesis).
    """
```

Two independent checks per row:
1. **Content check** — recompute `_hash_core(core_i, entry.prev_hash)` and compare
   to the stored `entry_hash`. Mismatch ⇒ row `i` was mutated after sealing.
2. **Link check** — `entry.prev_hash == entries[i-1].entry_hash` (row 0:
   `== GENESIS`). Mismatch ⇒ a row was deleted, inserted, or reordered.

Return a small dataclass `ChainCheck(ok: bool, broken_at: Optional[int], reason:
str)` so the caller can pinpoint the first broken link, not just get a boolean.

## What it catches — and the honest boundary

Catches: editing a decision/amount/reason after the fact (content check fails at
that row); deleting a row (link check fails at the next row); inserting a forged
row (link check fails); reordering rows (link check fails).

Does NOT catch on its own: a wholesale rewrite from genesis, where an attacker with
write access recomputes *every* hash forward. That's inherent to any self-contained
hash chain. Mitigation, in order of effort:
- **Demo-grade (do this):** surface the current head hash (`entries[-1].entry_hash`)
  on `/state` and in the CLI output. Anchoring the head somewhere the attacker
  can't retro-edit (read it aloud on the demo video, paste it in the submission) is
  enough to make rewrite detectable for judging.
- **Stretch:** write the head hash into Stripe transfer metadata on each settle, so
  the external Stripe dashboard independently witnesses the chain head. This makes
  the rewrite attack require forging Stripe records too — a genuinely strong story,
  and it ties the sponsor rail into the integrity claim.

Stating this boundary explicitly is the senior move. Don't claim immutability you
don't have; claim tamper-evidence you do.

## Tests to add (~5)

1. `verify()` returns ok on a normal operator run (chain is clean end to end).
2. Mutating a sealed `reason` in-place ⇒ `verify().ok is False`, `broken_at` points
   at that row, reason = mutated.
3. Deleting a middle row ⇒ `verify().ok is False`, broken_at = the row after it.
4. Reordering two rows ⇒ `verify().ok is False`.
5. **Regression guard:** calling `enrich()` (stamping `job`/`margin_killer`) leaves
   `verify().ok is True`. This proves the integrity boundary holds and protects the
   existing margin-killer feature from a future refactor that accidentally folds
   display fields into the hash.

Test 5 is the important one — it's the executable statement of the design decision.

## Demo surfacing (Alex's lane, not built here)

- Backend: add `ledger_head_hash` and `chain_verified: bool` to the `/state`
  payload (call `verify()` once per state read). Optionally a `GET /verify`
  endpoint.
- Dashboard: a small green "audit chain verified ✓ · head a3f9c2…" badge near the
  ledger. One sentence in the demo: "every decision is hash-chained; tamper with any
  row and the chain breaks — here's the live head." That's the RecoverOps-beating
  beat, on screen, in five seconds.

## Build order

1. Add the two fields + `GENESIS` + `_hash_core` + chain wiring in `record()`.
2. Add `verify()` + `ChainCheck`.
3. Add the 5 tests; run the full suite (must stay green — proves zero behaviour
   change to decisions).
4. Publish `ledger_head_hash` + `chain_verified` on `/state` (hand the badge to
   Alex).
