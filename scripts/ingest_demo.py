"""End-to-end invoice-ingestion demo: document in, governed decision out.

Runs the full P1 slice against a *running* Arbiter governance server:

    # one terminal:
    uvicorn arbiter.web.server:app
    # another:
    python scripts/ingest_demo.py

For each sample invoice it: reads the file, extracts the fields (real vision if
an inference key is set, else the deterministic mock), submits the proposed
payment to /authorize, and prints what governance decided. The two fixtures are
chosen to show both sides of the single money door:

  * acme_print_140.png  -> approved vendor      -> APPROVE (money moves)
  * shadow_900.png      -> vendor NOT on allowlist -> BLOCK (no money moves)

The point the demo makes: the agent gained a new *input* (a document) but no new
*power*. The unapproved invoice is blocked exactly as a typed payment to the same
vendor would be. Extraction proposes; governance disposes.

Offline note: the mock extractor returns a fixed reading regardless of the file,
so to demonstrate both paths without a key it is seeded per-fixture from the
known contents. With a real key set, the actual document is read and this seeding
is bypassed — the banner says which happened.
"""

from __future__ import annotations

import os
import sys

# Allow running as `python scripts/ingest_demo.py` from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from arbiter.ingest import ingest_invoice  # noqa: E402
from arbiter.ingest.invoice_extractor import (  # noqa: E402
    InvoiceExtraction,
    MockInvoiceExtractor,
    VisionInvoiceExtractor,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "invoices")

# What each fixture actually shows — used only to seed the offline mock so the
# demo can exercise both governance paths without an inference key. Mirrors the
# pixels in the committed PNGs. ``vendor_known`` / ``history`` model the caller's
# OWN records (an invoice can't vouch for itself): acme is an established
# allowlisted supplier; shadow is a stranger.
_SEED = {
    "acme_print_140.png": {
        "extraction": InvoiceExtraction(
            vendor_name="Acme Print Ltd", vendor_id="acme_print", amount=140.0,
            currency="GBP", invoice_ref="ACME-2026-0042", due_date="2026-07-15",
            confidence=0.95, backend="mock(seeded)",
        ),
        "vendor_known": True,
        "history": 11,
    },
    "shadow_900.png": {
        "extraction": InvoiceExtraction(
            vendor_name="Shadow Logistics", vendor_id="shadow_logistics", amount=900.0,
            currency="GBP", invoice_ref="SHX-99021", due_date="2026-07-01",
            confidence=0.93, backend="mock(seeded)",
        ),
        "vendor_known": False,
        "history": 0,
    },
}


def _extractor_for(filename: str):
    """Real vision when a key is set; otherwise a mock seeded from the fixture."""
    real = VisionInvoiceExtractor.from_env()
    if real is not None:
        return real
    return MockInvoiceExtractor(result=_SEED[filename]["extraction"])


def main() -> None:
    base_url = os.environ.get("ARBITER_BASE_URL", "http://127.0.0.1:8000")
    print(f"[demo] governance server: {base_url}")
    using_real = VisionInvoiceExtractor.from_env() is not None
    print(f"[demo] extractor: {'REAL vision (key set)' if using_real else 'MOCK (seeded from fixtures)'}\n")

    for filename in ("acme_print_140.png", "shadow_900.png"):
        path = os.path.join(FIXTURES, filename)
        seed = _SEED[filename]
        print("=" * 72)
        print(f"INVOICE: {filename}")
        result = ingest_invoice(
            path, base_url=base_url, extractor=_extractor_for(filename),
            vendor_known=seed["vendor_known"], vendor_history_count=seed["history"],
        )

        ex = result["extraction"]
        print(f"  read  -> vendor={ex['vendor_id']!r}  amount={ex['amount']}  "
              f"ref={ex['invoice_ref']!r}  via={ex['backend']}")

        status = result["status"]
        if status == "decided":
            d = result["decision"]
            print(f"  GOVERNANCE -> {d['decision'].upper()}  ({d['reason']})")
            print(f"  executed={d.get('executed')}  stripe_id={d.get('stripe_id')}  "
                  f"backend={d.get('stripe_backend')}")
        elif status == "unreadable":
            print(f"  UNREADABLE -> {result['reason']}")
        else:
            print(f"  {status.upper()} -> {result.get('error')}")
            if result.get("hint"):
                print(f"  hint: {result['hint']}")
        print()


if __name__ == "__main__":
    main()
