"""Invoice ingestion — turn a raw invoice into a structured payment request.

This is the front of the accounts-payable job: a business doesn't hand the agent
clean JSON, it hands it a PDF or a photo of an invoice. This module reads that
document and extracts the few fields the governance engine needs — vendor, amount,
currency, invoice reference — so the *same* three-layer pipeline (rules ->
Nemotron -> human) decides on it.

Two implementations behind one protocol, same discipline as the Stripe and
Nemotron layers:

  * ``MockInvoiceExtractor`` — deterministic, no key, no network. Returns a
                               canned extraction so the demo and tests run offline.
  * ``VisionInvoiceExtractor`` — a real multimodal model reads the document
                                 (OpenAI-compatible vision; works against the
                                 same OpenRouter/NVIDIA-style endpoints the
                                 Nemotron layer uses). Selected when a key is set.

Critical safety property: **extraction proposes, governance disposes.** Nothing
in here moves money or approves anything. The extracted vendor still has to be on
the owner's allowlist, the amount still runs through every rule. An invoice that
reads as a £900 payment to an unknown vendor produces a request that the engine
*blocks* — exactly as if a human had typed it. And the extractor never invents a
number it could not read: an unreadable amount comes back ``None``, never a guess,
because fabricating a figure on a financial document is the one thing a payment
system must never do.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

DEFAULT_VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl"
_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

_SYSTEM_PROMPT = (
    "You read a single supplier invoice and extract only what is printed on it. "
    "You never move money and you never approve anything; a separate governance "
    "engine decides whether to pay. Return a STRICT JSON object, exactly:\n"
    '{"vendor_name": "<as printed, or null>", '
    '"vendor_id": "<lowercase slug of the vendor, or null>", '
    '"amount": <number or null>, '
    '"currency": "<ISO 4217 code, e.g. GBP>", '
    '"invoice_ref": "<invoice number/reference, or null>", '
    '"due_date": "<YYYY-MM-DD or null>", '
    '"line_items": [{"description": "<text>", "amount": <number>}], '
    '"confidence": <0.0-1.0>}\n'
    "Rules:\n"
    "- Extract only what is actually on the document. If a field is missing or "
    "unreadable, return null for it. NEVER guess or invent a value — especially "
    "the amount. A wrong number on an invoice is worse than a null.\n"
    "- amount is the total payable (the invoice grand total), as a plain number "
    "with no currency symbol or thousands separators.\n"
    "- vendor_id is a stable lowercase slug of the vendor name (e.g. 'Acme "
    "Print Ltd' -> 'acme_print'), so the same supplier maps to the same id.\n"
    "- confidence reflects how clearly the document was readable, 0.0 to 1.0."
)


@dataclass(frozen=True)
class InvoiceExtraction:
    """Structured fields read off an invoice, ready for the governance engine.

    Every money/identity field is Optional and defaults to None: a field that
    was not clearly on the document is absent, never fabricated. ``source`` and
    ``backend`` record where the extraction came from so a test or the demo can
    tell a mock from a real model read.
    """

    vendor_name: Optional[str] = None
    vendor_id: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "GBP"
    invoice_ref: Optional[str] = None
    due_date: Optional[str] = None
    line_items: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    backend: str = "mock"
    notes: str = ""

    @property
    def is_payable(self) -> bool:
        """True only when the minimum needed to *propose* a payment is present.

        A proposal still has to survive governance; this just means the document
        yielded a vendor and a real amount to put in front of the engine. A
        missing amount or vendor means we cannot even form a request — we do not
        manufacture one.
        """
        return self.vendor_id is not None and self.amount is not None and self.amount > 0

    def to_authorize_payload(self, *, vendor_known: bool = False,
                             vendor_history_count: int = 0) -> dict:
        """Map the extraction onto the /authorize (AgentEvent) contract.

        Produces a ``vendor_payment`` request — paying a supplier's invoice is
        outbound accounts-payable, so it must run through the approved-payee
        allowlist (the allowlist rule keys on vendor_payment, not the inbound
        invoice_payment earn side). ``vendor_known`` / ``vendor_history_count``
        are supplied by the caller from its own records, not by the document — an
        invoice cannot vouch for itself. The extracted amount is passed as BOTH
        the amount to pay and the invoice_amount, so the engine's
        amount-vs-invoice mismatch rule has a baseline; a caller that holds a
        separately-stored invoice_amount can override it.

        Raises ValueError if the extraction is not payable — we refuse to build a
        money request out of a document we could not read, rather than defaulting
        a missing amount to something.
        """
        if not self.is_payable:
            raise ValueError(
                "InvoiceExtraction is not payable (missing vendor_id or amount) — "
                "refusing to fabricate a payment request from an unreadable invoice."
            )
        return {
            "kind": "vendor_payment",
            "amount": self.amount,
            "invoice_amount": self.amount,
            "currency": self.currency,
            "vendor_id": self.vendor_id,
            "vendor_known": vendor_known,
            "vendor_history_count": vendor_history_count,
            "ref": self.invoice_ref,
            "message": f"Ingested from invoice document (confidence={self.confidence:.2f}).",
        }


def _slug(name: Optional[str]) -> Optional[str]:
    """Lowercase slug of a vendor name, matching the model's vendor_id rule."""
    if not name:
        return None
    import re
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # Drop common company suffixes so 'Acme Print Ltd' and 'Acme Print' agree.
    for suffix in ("_ltd", "_limited", "_inc", "_llc", "_plc", "_gmbh"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s or None


def _coerce(raw: str) -> InvoiceExtraction:
    """Parse the model's JSON into an InvoiceExtraction, failing safe to empty.

    Malformed output, or any amount that isn't a positive number, yields an
    extraction with amount=None — which is_payable rejects. The parser never
    upgrades garbage into a number; the worst case is "we couldn't read it,"
    never "we read the wrong total."
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return InvoiceExtraction(backend="vision", confidence=0.0,
                                 notes="model output was not valid JSON")
    amount = data.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        amount = None
    vendor_name = data.get("vendor_name") or None
    vendor_id = data.get("vendor_id") or _slug(vendor_name)
    currency = (data.get("currency") or "GBP").upper()
    line_items = data.get("line_items") or []
    if not isinstance(line_items, list):
        line_items = []
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return InvoiceExtraction(
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        amount=float(amount) if amount is not None else None,
        currency=currency,
        invoice_ref=data.get("invoice_ref") or None,
        due_date=data.get("due_date") or None,
        line_items=line_items,
        confidence=max(0.0, min(1.0, confidence)),
        backend="vision",
    )


class InvoiceExtractor(Protocol):
    """The surface the ingestion path depends on. Both extractors satisfy it."""

    def extract(self, document: bytes, *, media_type: str = "image/png") -> InvoiceExtraction: ...


class MockInvoiceExtractor:
    """Deterministic extractor for offline demo and tests. Reads no document.

    Returns a fixed, payable extraction (an approved demo vendor) so the
    ingestion path runs end-to-end with no key and no network. Override the
    canned result to exercise other branches (unapproved vendor, unreadable).
    """

    backend = "mock"

    def __init__(self, result: Optional[InvoiceExtraction] = None) -> None:
        self._result = result or InvoiceExtraction(
            vendor_name="Acme Print Ltd",
            vendor_id="acme_print",
            amount=140.0,
            currency="GBP",
            invoice_ref="ACME-2026-0042",
            due_date="2026-07-15",
            line_items=[{"description": "Q3 brochure print run", "amount": 140.0}],
            confidence=0.95,
            backend="mock",
        )

    def extract(self, document: bytes, *, media_type: str = "image/png") -> InvoiceExtraction:
        return self._result


class VisionInvoiceExtractor:
    """Reads a real invoice with a multimodal model (OpenAI-compatible vision).

    Uses the same SDK + endpoint family as the Nemotron layer, so a single
    inference key (NVIDIA NIM or an OpenRouter fallback) powers both. The model
    only reads and structures the document; it has no tools and moves no money.
    A network/parse failure fails safe to an empty (non-payable) extraction.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_VISION_MODEL,
                 base_url: str = _DEFAULT_BASE_URL, timeout: float = 60.0) -> None:
        from openai import OpenAI

        self.model = model
        self.base_url = base_url
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @property
    def backend(self) -> str:
        host = self.base_url.lower()
        if "integrate.api.nvidia.com" in host:
            return "vision:NVIDIA"
        if "openrouter.ai" in host:
            return "vision:OpenRouter"
        return f"vision:{self.base_url}"

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> Optional["VisionInvoiceExtractor"]:
        """Build from the same keys the Nemotron layer uses, or None if absent.

        Honors NVIDIA_NIM_BASE_URL so the OpenRouter fallback that unblocks
        Nemotron also powers invoice vision with no extra config.
        """
        key = (
            os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("NVIDIA_NIM_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        if not key:
            return None
        base_url = os.environ.get("NVIDIA_NIM_BASE_URL", _DEFAULT_BASE_URL)
        chosen = model or os.environ.get("INVOICE_VISION_MODEL", DEFAULT_VISION_MODEL)
        return cls(api_key=key, model=chosen, base_url=base_url)

    def extract(self, document: bytes, *, media_type: str = "image/png") -> InvoiceExtraction:
        b64 = base64.b64encode(document).decode("ascii")
        data_url = f"data:{media_type};base64,{b64}"
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Extract this invoice as the strict JSON object."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ],
                temperature=0.0,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 — unreadable beats wrong; never raise a number
            return InvoiceExtraction(
                backend=self.backend, confidence=0.0,
                notes=f"vision call failed ({type(exc).__name__}) — no fields extracted.",
            )
        result = _coerce(raw)
        # Preserve which real endpoint served the read for honest reporting.
        return InvoiceExtraction(
            vendor_name=result.vendor_name, vendor_id=result.vendor_id,
            amount=result.amount, currency=result.currency,
            invoice_ref=result.invoice_ref, due_date=result.due_date,
            line_items=result.line_items, confidence=result.confidence,
            backend=self.backend, notes=result.notes,
        )


def select_invoice_extractor(model: Optional[str] = None) -> InvoiceExtractor:
    """Real vision extractor when an inference key is set, else the mock.

    Prints which backend is active at construction so the demo shows, honestly,
    whether an invoice was read by a real model or served from the canned mock —
    the same banner discipline as the Stripe and Nemotron layers.
    """
    vision = VisionInvoiceExtractor.from_env(model=model)
    if vision is not None:
        print(f"[arbiter] Invoice ingestion: REAL {vision.backend} ({vision.model})")
        return vision
    print("[arbiter] Invoice ingestion: MockInvoiceExtractor (no inference key set)")
    return MockInvoiceExtractor()
