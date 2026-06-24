"""Invoice ingestion path — drop a document, get a governed decision.

Ties the two halves together: read an invoice with the extractor, then submit the
proposed payment to the *same* governance pipeline every other payment goes
through. The agent that ingests an invoice gains no new power — it still cannot
pay a vendor the owner didn't approve, still gets blocked on a mismatch, still
escalates a detail change. The document is just a new *input* to governance, not
a new *door* around it.

Two entry points:

  * ``extract_invoice(path)`` — read a file into an ``InvoiceExtraction`` (pure;
    no network beyond the model read, no money).
  * ``ingest_invoice(path, base_url=...)`` — extract, then POST the proposed
    payment to a running Arbiter governance server and return its decision. This
    is the full vertical slice the demo films.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from .invoice_extractor import (
    InvoiceExtraction,
    InvoiceExtractor,
    select_invoice_extractor,
)

# Map a file extension to the media type a vision model expects. PDFs are sent
# as application/pdf; endpoints that can't read PDF directly will fail safe to a
# non-payable extraction (unreadable beats wrong), and the caller can rasterize.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

_DEFAULT_BASE_URL = os.environ.get("ARBITER_BASE_URL", "http://127.0.0.1:8000")
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)


def _media_type_for(path: str) -> str:
    _, ext = os.path.splitext(path.lower())
    return _MEDIA_TYPES.get(ext, "image/png")


def extract_invoice(path: str, extractor: Optional[InvoiceExtractor] = None) -> InvoiceExtraction:
    """Read an invoice file into a structured extraction.

    Uses the provided extractor, or selects one from the environment (real vision
    when an inference key is set, else the offline mock). Raises FileNotFoundError
    if the path doesn't exist — that's a caller error, distinct from an
    unreadable document, which comes back as a non-payable extraction.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"invoice file not found: {path}")
    with open(path, "rb") as fh:
        document = fh.read()
    ex = extractor or select_invoice_extractor()
    return ex.extract(document, media_type=_media_type_for(path))


def ingest_invoice(
    path: str,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    extractor: Optional[InvoiceExtractor] = None,
    vendor_known: bool = False,
    vendor_history_count: int = 0,
) -> dict:
    """Extract an invoice and run the proposed payment through governance.

    Returns a dict that always carries the extraction so the caller can see what
    was read, plus either the governance ``decision`` (when the invoice was
    payable and the server answered) or a ``status``/``error`` explaining why no
    decision was reached. Never raises on an unreadable invoice or a down server:
    the failure modes are *data*, not exceptions, so a demo or an agent can react
    to them instead of crashing.
    """
    extraction = extract_invoice(path, extractor=extractor)
    base = {
        "extraction": {
            "vendor_id": extraction.vendor_id,
            "vendor_name": extraction.vendor_name,
            "amount": extraction.amount,
            "currency": extraction.currency,
            "invoice_ref": extraction.invoice_ref,
            "due_date": extraction.due_date,
            "confidence": extraction.confidence,
            "backend": extraction.backend,
        },
    }

    if not extraction.is_payable:
        # We could not read a vendor + amount. We do NOT manufacture a request;
        # we report that the document was unreadable and stop. No money path.
        return {
            **base,
            "status": "unreadable",
            "reason": (
                "Invoice did not yield a vendor and a positive amount; no payment "
                "request was created. " + (extraction.notes or "")
            ).strip(),
        }

    payload = extraction.to_authorize_payload(
        vendor_known=vendor_known, vendor_history_count=vendor_history_count
    )

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{base_url}/authorize", json=payload)
        resp.raise_for_status()
    except httpx.ConnectError:
        return {
            **base,
            "status": "server_unreachable",
            "proposed_payment": payload,
            "error": f"Arbiter governance server not reachable at {base_url}.",
            "hint": "Start it: uvicorn arbiter.web.server:app  (or set ARBITER_BASE_URL).",
        }
    except httpx.HTTPStatusError as exc:
        return {
            **base,
            "status": "rejected",
            "proposed_payment": payload,
            "error": f"governance server returned {exc.response.status_code}",
            "detail": exc.response.text[:500],
        }

    decision = resp.json()
    return {
        **base,
        "status": "decided",
        "proposed_payment": payload,
        "decision": decision,
    }
