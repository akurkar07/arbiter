"""Integration test for the Hermes-native authorize path.

Boots the real web server in-process (TestClient), then drives the same code
path the Arbiter MCP server uses: POST /authorize -> 3-layer engine -> ledger.
Proves approve / block / escalate all flow through correctly and that an
escalation genuinely blocks until a human resolves the gate.

This is the contract the MCP server (arbiter/mcp_server.py) depends on, so it
guards the Hermes-native integration against regressions.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.web.server import app, state


@pytest.fixture(autouse=True)
def _fresh_state():
    """Each test starts on a clean ledger."""
    state.reset()
    yield
    # Release any thread still parked on the gate so it can exit cleanly.
    if state.running:
        state.escalation.resolve(DecisionKind.BLOCK)


@pytest.fixture
def client():
    return TestClient(app)


def test_authorize_approves_normal_invoice(client):
    resp = client.post("/authorize", json={
        "kind": "invoice_payment", "amount": 250.0, "invoice_amount": 250.0, "ref": "inv-1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["decided_by"] == "rules"


def test_authorize_blocks_over_budget_self_spend(client):
    resp = client.post("/authorize", json={
        "kind": "self_spend", "amount": 5000.0, "category": "fraud_detection", "ref": "spend-1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "block"
    assert body["decided_by"] == "rules"


def test_authorize_rejects_unknown_kind(client):
    resp = client.post("/authorize", json={"kind": "not_a_real_kind", "amount": 1.0})
    assert resp.status_code == 422
    assert "valid_kinds" in resp.json()


def test_authorize_escalation_blocks_until_human_approves(client):
    """The core trust beat: an escalating payment pauses until a human taps approve."""
    result_box = {}

    def call():
        r = client.post("/authorize", json={
            "kind": "vendor_detail_change", "vendor_id": "globex", "vendor_known": True,
            "vendor_history_count": 8, "detail_change_evidence": 0.3, "amount": 900.0, "ref": "bank-1",
        })
        result_box["body"] = r.json()

    t = threading.Thread(target=call)
    t.start()

    # The call must NOT have returned yet — it's blocked on the human gate.
    pending_id = None
    for _ in range(50):
        time.sleep(0.1)
        pending = client.get("/state").json().get("awaiting_approval")
        if pending:
            pending_id = pending["event_id"]
            break
    assert pending_id, "escalation never published a pending approval card"
    assert "body" not in result_box, "call returned before the human resolved the gate"

    # Human taps approve (GET, like a phone SMS link).
    client.get(f"/approve/{pending_id}")
    t.join(timeout=10)

    assert result_box["body"]["decision"] == "approve"
    assert result_box["body"]["decided_by"] == "escalate"


def test_authorize_decisions_land_in_shared_ledger(client):
    """MCP-driven decisions must show up on the same /state the dashboard polls."""
    client.post("/authorize", json={
        "kind": "invoice_payment", "amount": 100.0, "invoice_amount": 100.0,
        "ref": "led-1", "event_id": "led-1",
    })
    timeline = client.get("/state").json()["timeline"]
    assert len(timeline) == 1
    assert timeline[0]["id"] == "led-1"
    assert timeline[0]["decision"] == "approve"


def test_authorize_blocks_unapproved_payee(client):
    """The Hermes seam carries the allowlist: an off-list payee is blocked.

    This is the governance a Hermes agent inherits the moment it's handed
    Arbiter — it physically cannot pay a supplier the owner never approved,
    even though the agent itself drove the request.
    """
    resp = client.post("/authorize", json={
        "kind": "vendor_payment", "vendor_id": "meta_ads", "amount": 300.0,
        "invoice_amount": 300.0, "vendor_known": True, "ref": "agent-meta-1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "block"
    assert "payee_not_approved" in body["policy_refs"]
    assert body["decided_by"] == "rules"
    # Settlement truth: a block moves no money and produces no rail handle.
    assert body["executed"] is False
    assert body["stripe_id"] is None


def test_authorize_approves_allowlisted_supplier(client):
    """An approved, established supplier paid the reconciled amount: the agent
    is cleared to pay — autopilot, no human needed. settle() executes the
    payment in the same call, so the response reports it as executed."""
    resp = client.post("/authorize", json={
        "kind": "vendor_payment", "vendor_id": "aws", "amount": 220.0,
        "invoice_amount": 220.0, "vendor_known": True, "vendor_history_count": 11,
        "ref": "agent-aws-1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approve"
    assert "approved_supplier_payment" in body["policy_refs"]
    # The agent didn't get a permission slip — Arbiter moved the money.
    assert body["executed"] is True
