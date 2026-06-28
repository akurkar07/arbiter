"""Audit evidence packet contract."""

import pytest
from fastapi.testclient import TestClient

from arbiter.ledger import EventLedger
from arbiter.models import AgentEvent, DecisionKind, DecisionLayer, EventKind, PolicyResult
from arbiter.web.server import app, state


@pytest.fixture(autouse=True)
def _fresh_state():
    state.reset()
    yield
    if state.running:
        state.escalation.resolve(DecisionKind.BLOCK)
    if state.thread is not None:
        state.thread.join(timeout=2)


@pytest.fixture
def client():
    return TestClient(app)


def _approve(reason: str = "ok") -> PolicyResult:
    return PolicyResult(
        decision=DecisionKind.APPROVE,
        reason=reason,
        policy_refs=["invoice_normal_paid"],
        risk_score=0.05,
        decided_by=DecisionLayer.RULES,
    )


def test_ledger_audit_summary_hash_changes_when_entries_change():
    ledger = EventLedger()
    empty = ledger.audit_summary()
    assert empty["event_count"] == 0
    assert empty["ledger_hash"] is None
    assert empty["append_only"] is True

    event = AgentEvent(kind=EventKind.INVOICE_PAYMENT, amount=42.0, invoice_amount=42.0, ref="inv-42")
    ledger.record(event, _approve(), "evt-1", "invoice paid", book_money=True)
    first = ledger.audit_summary()
    ledger.record(event, _approve("second"), "evt-2", "invoice paid again", book_money=True)
    second = ledger.audit_summary()

    assert first["event_count"] == 1
    assert second["event_count"] == 2
    assert first["ledger_hash"] != second["ledger_hash"]
    assert second["chain"][1]["prev_hash"] == first["ledger_hash"]


def test_state_exposes_audit_evidence_packet(client):
    body = client.post("/authorize", json={
        "kind": "invoice_payment",
        "amount": 42.0,
        "invoice_amount": 42.0,
        "ref": "audit-invoice-42",
        "event_id": "audit-evt-1",
    }).json()
    assert body["decision"] == "approve"

    snap = client.get("/state").json()
    audit = snap["audit_evidence"]
    assert audit["event_count"] == 1
    assert audit["ledger_hash"]
    assert audit["hash_algorithm"] == "sha256-prev-hash"
    assert audit["single_money_door"] == "ArbiterAgent.settle()"
    assert audit["policy_source"] == "backend PolicyContext"
    assert audit["chain"][0]["event_id"] == "audit-evt-1"
