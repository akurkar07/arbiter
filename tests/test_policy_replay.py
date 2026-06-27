"""Policy Replay / what-if simulator contract."""

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.web.server import app, state


@pytest.fixture(autouse=True)
def _fresh_state():
    state.owner_policy = {
        "spend_cap": 1000.0,
        "budget_remaining": 1000.0,
        "allowed_categories": ["fraud_detection", "ocr", "bank_reconciliation"],
        "approved_payees": ["aws", "northstar_studio", "acme_print"],
        "duplicate_lookback": 50,
        "new_vendor_auto_threshold": 50.0,
        "detail_change_evidence_threshold": 0.8,
    }
    state.reset()
    yield
    if state.running:
        state.escalation.resolve(DecisionKind.BLOCK)
    if state.thread is not None:
        state.thread.join(timeout=2)


@pytest.fixture
def client():
    return TestClient(app)


AWS_PAYMENT = {
    "kind": "vendor_payment",
    "vendor_id": "aws",
    "amount": 42.0,
    "invoice_amount": 42.0,
    "vendor_known": True,
    "vendor_history_count": 12,
    "ref": "aws-hosting-042",
}


def test_policy_replay_compares_current_and_proposed_policy(client):
    body = client.post("/policy/replay", json={
        "event": AWS_PAYMENT,
        "policy": {"approved_payees": ["acme_print"]},
    }).json()

    assert body["baseline"]["decision"] == "approve"
    assert body["replay"]["decision"] == "block"
    assert "payee_not_approved" in body["replay"]["policy_refs"]
    assert body["changed"] is True
    assert body["moved_money"] is False
    assert body["mutated_state"] is False


def test_policy_replay_does_not_mutate_owner_policy_or_ledger(client):
    before_policy = client.get("/policy").json()["policy"]
    before_timeline = client.get("/state").json()["timeline"]

    resp = client.post("/policy/replay", json={
        "event": AWS_PAYMENT,
        "policy": {"approved_payees": ["acme_print"]},
    })

    assert resp.status_code == 200
    assert client.get("/policy").json()["policy"] == before_policy
    assert client.get("/state").json()["timeline"] == before_timeline


def test_policy_replay_can_show_category_policy_changes(client):
    body = client.post("/policy/replay", json={
        "event": {
            "kind": "self_spend",
            "amount": 60.0,
            "category": "marketing",
            "ref": "ad-campaign-tool",
        },
        "policy": {"allowed_categories": ["fraud_detection", "ocr", "bank_reconciliation", "marketing"]},
    }).json()

    assert body["baseline"]["decision"] == "block"
    assert body["replay"]["decision"] == "approve"
    assert body["changed"] is True


def test_policy_replay_rejects_bad_policy(client):
    resp = client.post("/policy/replay", json={
        "event": AWS_PAYMENT,
        "policy": {"spend_cap": -1},
    })

    assert resp.status_code == 422
    assert "spend_cap" in resp.json()["error"]


def test_policy_replay_rejects_bad_event(client):
    resp = client.post("/policy/replay", json={
        "event": {"kind": "wire_the_moon", "amount": 42},
        "policy": {},
    })

    assert resp.status_code == 422
    assert "unknown event kind" in resp.json()["error"]
