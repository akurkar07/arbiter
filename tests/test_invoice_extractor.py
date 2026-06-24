"""Invoice extractor: lock the read + the no-fabrication guarantee.

These tests pin the contract the rest of the AP pipeline trusts:

  * the mock reads deterministically (offline demo + tests need no key);
  * a payable extraction maps onto the /authorize (vendor_payment) contract;
  * an unreadable invoice NEVER becomes a payment request — the one behaviour a
    payment system must never get wrong is inventing a number, so a missing
    amount fails closed (is_payable False, to_authorize_payload raises);
  * the JSON coercion never upgrades garbage into a figure.
"""

from __future__ import annotations

import pytest

from arbiter.ingest.invoice_extractor import (
    InvoiceExtraction,
    MockInvoiceExtractor,
    VisionInvoiceExtractor,
    _coerce,
    _slug,
    select_invoice_extractor,
)


def test_mock_extracts_deterministically():
    """The mock returns a fixed, payable reading regardless of input bytes."""
    ex = MockInvoiceExtractor()
    a = ex.extract(b"anything")
    b = ex.extract(b"something else")
    assert a == b
    assert a.is_payable
    assert a.vendor_id == "acme_print"
    assert a.amount == 140.0


def test_payable_extraction_maps_to_vendor_payment():
    """A readable invoice becomes a vendor_payment request (runs the allowlist)."""
    ex = InvoiceExtraction(vendor_id="acme_print", amount=140.0, currency="GBP",
                           invoice_ref="ACME-1", confidence=0.9)
    payload = ex.to_authorize_payload(vendor_known=True, vendor_history_count=11)
    # vendor_payment is the kind the approved-payee allowlist rule keys on.
    assert payload["kind"] == "vendor_payment"
    assert payload["amount"] == 140.0
    assert payload["invoice_amount"] == 140.0  # baseline for the mismatch rule
    assert payload["vendor_id"] == "acme_print"
    assert payload["ref"] == "ACME-1"
    assert payload["vendor_known"] is True
    assert payload["vendor_history_count"] == 11


def test_unreadable_amount_is_not_payable():
    """No amount -> not payable. We cannot form a request we couldn't read."""
    ex = InvoiceExtraction(vendor_id="acme_print", amount=None)
    assert ex.is_payable is False


def test_unreadable_vendor_is_not_payable():
    """No vendor -> not payable, even with an amount."""
    ex = InvoiceExtraction(vendor_id=None, amount=140.0)
    assert ex.is_payable is False


def test_to_authorize_payload_refuses_to_fabricate():
    """The core no-fabrication guarantee: an unreadable invoice raises rather
    than defaulting a missing amount to anything."""
    ex = InvoiceExtraction(vendor_id="acme_print", amount=None)
    with pytest.raises(ValueError, match="not payable"):
        ex.to_authorize_payload()


def test_zero_and_negative_amounts_are_not_payable():
    """A zero or negative total is not a real payable amount."""
    assert InvoiceExtraction(vendor_id="x", amount=0.0).is_payable is False
    assert InvoiceExtraction(vendor_id="x", amount=-5.0).is_payable is False


def test_coerce_rejects_non_json():
    """Garbage model output coerces to an empty, non-payable extraction."""
    ex = _coerce("this is not json")
    assert ex.amount is None
    assert ex.is_payable is False


def test_coerce_rejects_non_numeric_amount():
    """An amount the model wrote as text is dropped, not parsed into a number."""
    ex = _coerce('{"vendor_id": "acme_print", "amount": "one hundred"}')
    assert ex.amount is None
    assert ex.is_payable is False


def test_coerce_rejects_negative_amount():
    """A negative amount is dropped to None — never a payable figure."""
    ex = _coerce('{"vendor_id": "acme_print", "amount": -100}')
    assert ex.amount is None


def test_coerce_accepts_a_clean_reading():
    """A well-formed reading coerces into a payable extraction."""
    ex = _coerce('{"vendor_name": "Acme Print Ltd", "vendor_id": "acme_print", '
                 '"amount": 140.0, "currency": "gbp", "invoice_ref": "ACME-1", '
                 '"confidence": 0.9}')
    assert ex.is_payable
    assert ex.vendor_id == "acme_print"
    assert ex.amount == 140.0
    assert ex.currency == "GBP"  # normalised upper-case
    assert ex.backend == "vision"


def test_coerce_derives_vendor_id_from_name_when_missing():
    """If the model gives a name but no id, we slug the name into an id."""
    ex = _coerce('{"vendor_name": "Acme Print Ltd", "amount": 140.0}')
    assert ex.vendor_id == "acme_print"


def test_coerce_clamps_confidence():
    """Out-of-range confidence is clamped to [0, 1]."""
    assert _coerce('{"amount": 1, "vendor_id": "x", "confidence": 5}').confidence == 1.0
    assert _coerce('{"amount": 1, "vendor_id": "x", "confidence": -2}').confidence == 0.0


@pytest.mark.parametrize("name,expected", [
    ("Acme Print Ltd", "acme_print"),
    ("Shadow Logistics", "shadow_logistics"),
    ("NorthStar Studio", "northstar_studio"),
    ("AWS", "aws"),
    ("Foo Bar Limited", "foo_bar"),
    (None, None),
    ("", None),
])
def test_slug_is_stable(name, expected):
    assert _slug(name) == expected


def test_select_returns_mock_without_key(monkeypatch):
    """No inference key anywhere -> the offline mock is selected."""
    for var in ("NVIDIA_API_KEY", "NVIDIA_NIM_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    ex = select_invoice_extractor()
    assert isinstance(ex, MockInvoiceExtractor)


def test_vision_from_env_none_without_key(monkeypatch):
    """The real extractor declines to build when no key is present."""
    for var in ("NVIDIA_API_KEY", "NVIDIA_NIM_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert VisionInvoiceExtractor.from_env() is None
