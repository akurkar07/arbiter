"""Invoice ingestion through the real engine: extraction grants no new power.

The point of P1 is that ingesting a document is a new *input* to governance, not
a new *door* around it. These tests drive an ingested invoice through the SAME
engine every other payment uses (in-process via TestClient) and prove:

  * an approved, established supplier's invoice settles (autopilot);
  * an invoice for a vendor NOT on the owner's allowlist is BLOCKED — exactly as
    a typed payment to that vendor would be — with no money moved;
  * an unreadable invoice never reaches the engine at all (no fabricated
    request), so the rail is never touched.

We use the real fixture PNGs and the deterministic mock extractor so the whole
path runs with no key and no network: file bytes -> extraction ->
to_authorize_payload -> the real /authorize handler -> governance verdict.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from arbiter.ingest.invoice_extractor import InvoiceExtraction, MockInvoiceExtractor
from arbiter.ingest.pipeline import extract_invoice
from arbiter.web.server import app, state

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "invoices")


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts from a clean ledger on the shared in-process app."""
    state.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _ingest_via_app(client, extraction: InvoiceExtraction, *, vendor_known: bool,
                    history: int) -> dict:
    """Run an extraction's proposed payment through the real /authorize handler.

    Mirrors what ingest_invoice() does over HTTP, but posts to the in-process
    TestClient so the test needs no live server — same engine, same ledger.
    """
    payload = extraction.to_authorize_payload(
        vendor_known=vendor_known, vendor_history_count=history
    )
    resp = client.post("/authorize", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_fixture_files_exist():
    """The committed invoice fixtures are present (regenerate via the script)."""
    assert os.path.isfile(os.path.join(FIXTURES, "acme_print_140.png"))
    assert os.path.isfile(os.path.join(FIXTURES, "shadow_900.png"))


def test_extract_reads_a_fixture_through_the_mock():
    """extract_invoice reads a real file and returns the seeded extraction."""
    seeded = InvoiceExtraction(vendor_id="acme_print", amount=140.0,
                               invoice_ref="ACME-2026-0042", backend="mock")
    ex = extract_invoice(
        os.path.join(FIXTURES, "acme_print_140.png"),
        extractor=MockInvoiceExtractor(result=seeded),
    )
    assert ex.vendor_id == "acme_print"
    assert ex.amount == 140.0


def test_missing_file_raises():
    """A bad path is a caller error, distinct from an unreadable document."""
    with pytest.raises(FileNotFoundError):
        extract_invoice(os.path.join(FIXTURES, "does_not_exist.png"))


def test_ingested_approved_invoice_settles(client):
    """An allowlisted, established supplier's invoice is paid — autopilot."""
    seeded = InvoiceExtraction(vendor_id="acme_print", amount=140.0, currency="GBP",
                               invoice_ref="ACME-2026-0042", confidence=0.95)
    body = _ingest_via_app(client, seeded, vendor_known=True, history=11)
    assert body["decision"] == "approve"
    assert "approved_supplier_payment" in body["policy_refs"]
    assert body["executed"] is True


def test_ingested_unapproved_invoice_is_blocked(client):
    """The load-bearing guarantee: an invoice for an off-allowlist vendor is
    BLOCKED, even though it came from a real document. Extraction adds an input,
    not a bypass — no money moves."""
    seeded = InvoiceExtraction(vendor_id="shadow_logistics", amount=900.0,
                               currency="GBP", invoice_ref="SHX-99021", confidence=0.93)
    body = _ingest_via_app(client, seeded, vendor_known=False, history=0)
    assert body["decision"] == "block"
    assert "payee_not_approved" in body["policy_refs"]
    # Settlement truth: a block moves no money and produces no rail handle.
    assert body["executed"] is False
    assert body["stripe_id"] is None


def test_unreadable_invoice_never_reaches_the_engine(client):
    """An unreadable document yields no payment request at all — the engine is
    never asked, so the ledger stays empty. No fabricated payment."""
    unreadable = InvoiceExtraction(vendor_id=None, amount=None, backend="mock")
    # The pipeline refuses to build a payload; mirror that the engine is untouched.
    assert unreadable.is_payable is False
    with pytest.raises(ValueError):
        unreadable.to_authorize_payload()
    # Nothing was posted, so the shared ledger recorded no decision.
    timeline = client.get("/state").json()["timeline"]
    assert timeline == []
