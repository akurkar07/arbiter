# Key drop spec — for Atlas

**Target:** helios-prod (the box running Arbiter)
**File:** `/root/arbiter.env`  (already exists, perms `600`, root-only, OUTSIDE the git tree — cannot be committed)
**Delivery:** machine-to-machine only (SSH into helios-prod and edit the file directly). **Never paste key values into any Discord/chat channel.**

The Arbiter code now reads these at boot via `select_stripe()` and
`select_nemotron()` and prints an honest banner for each (REAL vs stub/mock).

## What goes in the file

### 1. Stripe (required for the real money-out demo)

```
STRIPE_SECRET_KEY=sk_test_...        # MUST start sk_test_ — the app refuses any non-test key
```

**Dashboard prerequisite (one-time, free, instant):** in the Stripe test
dashboard, enable **Connect → Get started**. The pay path now uses Connect
**transfers** (`tr_...`) to a connected supplier account — NOT the old
OutboundPayments/Treasury path. Without Connect enabled the transfer call returns
a capability error (recorded, not crashing), so the demo silently falls back to
"recorded only" — which is exactly what we're trying to get past.

**Do NOT set `STRIPE_FINANCIAL_ACCOUNT`** — the Treasury path is gone. It's
ignored now; leave it unset.

Optional (only if the test account isn't GB/GBP):
```
STRIPE_PLATFORM_COUNTRY=GB           # connected-account country, default GB
STRIPE_PLATFORM_CURRENCY=gbp         # transfer/balance currency, default gbp
```

### 2. NVIDIA Nemotron — primary path

```
NVIDIA_API_KEY=nvapi-...             # API-catalog key from the build.nvidia.com MODEL PAGE
```

The existing key 403s because it's a personal NGC key with no inference
entitlement. Regenerate from the **model page** ("Get API Key" / "Build with this
NIM"), not from ngc.nvidia.com. The new key authenticates on BOTH `/models` and
`/chat/completions`.

### 2b. NVIDIA Nemotron — fallback if the key still 403s

If a correct NVIDIA key can't be obtained in time, point the SAME client at
OpenRouter's free Nemotron instead (the code supports this now, no code change):

```
OPENROUTER_API_KEY=sk-or-...                       # from openrouter.ai/keys
NVIDIA_NIM_BASE_URL=https://openrouter.ai/api/v1   # redirects the client
NVIDIA_NIM_MODEL=nvidia/nemotron-...:free          # pick a free nemotron id from /models
```

To find a current free Nemotron id (no auth needed):
```bash
curl -s https://openrouter.ai/api/v1/models \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'nemotron' in m['id'].lower()]"
```

The boot banner will then read `REAL OpenRouter (<model>)` instead of
`REAL NVIDIA NIM` — honest about which endpoint actually served the judgement.

## Final file shape

Minimum (primary paths, both sponsors real):
```
STRIPE_SECRET_KEY=sk_test_...
NVIDIA_API_KEY=nvapi-...
```

Or with the NVIDIA fallback instead of a working NVIDIA key:
```
STRIPE_SECRET_KEY=sk_test_...
OPENROUTER_API_KEY=sk-or-...
NVIDIA_NIM_BASE_URL=https://openrouter.ai/api/v1
NVIDIA_NIM_MODEL=nvidia/nemotron-...:free
```

No quotes, no spaces around `=`, no trailing blank lines. Re-run `chmod 600
/root/arbiter.env` after editing.

## Verify (neither command prints a key)

From `/root/helios-workspace/arbiter`:

```bash
set -a; . /root/arbiter.env; set +a

# NVIDIA / OpenRouter — want exit 0 and "OK: real Nemotron returned a valid bounded decision."
.venv/bin/python -m arbiter.agent.nim_nemotron; echo "nim exit=$?"

# Stripe — want "[arbiter] Stripe layer: REAL test-mode (sk_test_...)"
.venv/bin/python -c "from arbiter.stripe_glue import select_stripe; select_stripe()"
```

- NIM prints `FAIL`/exit 2 → key still wrong type (regenerate from model page) or
  the OpenRouter model id isn't a valid free id.
- Stripe prints `stub (refusing non-test key)` → not an `sk_test_` key.

Once both pass, ping Helios — the live proof run (a real `tr_...` transfer + a
real bounded Nemotron decision, captured for the video) starts from there.
