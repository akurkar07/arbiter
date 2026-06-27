"""Trust controls for safe autonomy mode.

These tests lock the rule that trust modes only make execution stricter. They
must never turn a blocked policy decision into an executed payment, and they
must never book spend in the ledger unless settlement truth says money moved.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.operator import BusinessOperator, Job, SpendStatus, ToolPurchase
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


def _approved_supplier_payload(ref: str = "trust-aws-1") -> dict:
    return {
        "kind": "vendor_payment",
        "vendor_id": "aws",
        "amount": 42.0,
        "invoice_amount": 42.0,
        "vendor_known": True,
        "vendor_history_count": 12,
        "ref": ref,
        "event_id": ref,
    }


def test_state_exposes_default_trust_mode(client):
    snap = client.get("/state").json()
    assert snap["trust_mode"] == "policy_autopilot"
    assert "policy_autopilot" in snap["trust_controls"]["modes"]


def test_rejects_unknown_trust_mode(client):
    resp = client.post("/trust_mode", json={"mode": "YOLO"})
    assert resp.status_code == 422
    assert "valid_modes" in resp.json()
    assert client.get("/state").json()["trust_mode"] == "policy_autopilot"


def test_policy_autopilot_executes_approved_policy_decision(client):
    resp = client.post("/authorize", json=_approved_supplier_payload())
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["executed"] is True

    snap = client.get("/state").json()
    assert snap["supplier_payments"]
    assert snap["spend"] == 42.0


def test_monitor_only_records_approval_without_execution_or_ledger_spend(client):
    client.post("/trust_mode", json={"mode": "monitor_only"})
    resp = client.post("/authorize", json=_approved_supplier_payload())
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["executed"] is False
    assert body["stripe_id"] is None
    assert "trust_monitor_only" in body["policy_refs"]

    snap = client.get("/state").json()
    assert snap["supplier_payments"] == []
    assert snap["spend"] == 0.0


def test_monitor_only_does_not_override_a_policy_block(client):
    client.post("/trust_mode", json={"mode": "monitor_only"})
    resp = client.post("/authorize", json={
        "kind": "vendor_payment",
        "vendor_id": "not_approved",
        "amount": 42.0,
        "invoice_amount": 42.0,
        "vendor_known": True,
        "vendor_history_count": 12,
        "ref": "trust-block-1",
    })
    body = resp.json()
    assert body["decision"] == "block"
    assert body["executed"] is False
    assert "payee_not_approved" in body["policy_refs"]


def test_approval_required_blocks_before_execution_until_owner_approves(client):
    client.post("/trust_mode", json={"mode": "approval_required"})
    result_box = {}

    def call():
        result_box["body"] = client.post("/authorize", json=_approved_supplier_payload()).json()

    t = threading.Thread(target=call)
    t.start()

    pending_id = None
    for _ in range(50):
        time.sleep(0.1)
        pending = client.get("/state").json().get("awaiting_approval")
        if pending:
            pending_id = pending["event_id"]
            break

    assert pending_id == "trust-aws-1"
    assert "body" not in result_box
    snap = client.get("/state").json()
    assert snap["supplier_payments"] == []
    assert snap["spend"] == 0.0

    client.post(f"/approve/{pending_id}")
    t.join(timeout=5)

    body = result_box["body"]
    assert body["decision"] == "approve"
    assert body["executed"] is True
    snap = client.get("/state").json()
    assert snap["supplier_payments"]
    assert snap["spend"] == 42.0


def test_approval_required_owner_denial_prevents_execution(client):
    client.post("/trust_mode", json={"mode": "approval_required"})
    result_box = {}

    def call():
        result_box["body"] = client.post("/authorize", json=_approved_supplier_payload("trust-deny-1")).json()

    t = threading.Thread(target=call)
    t.start()

    pending_id = None
    for _ in range(50):
        time.sleep(0.1)
        pending = client.get("/state").json().get("awaiting_approval")
        if pending:
            pending_id = pending["event_id"]
            break

    assert pending_id == "trust-deny-1"
    client.post(f"/deny/{pending_id}")
    t.join(timeout=5)

    body = result_box["body"]
    assert body["decision"] == "block"
    assert body["executed"] is False
    assert "trust_approval_denied" in body["policy_refs"]
    snap = client.get("/state").json()
    assert snap["supplier_payments"] == []
    assert snap["spend"] == 0.0


def test_paused_mode_does_not_start_operator_or_execute(client):
    client.post("/trust_mode", json={"mode": "paused"})
    run = client.post("/run_operator").json()
    assert run["running"] is False
    assert client.get("/state").json()["running"] is False

    resp = client.post("/authorize", json=_approved_supplier_payload("trust-paused-1"))
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["executed"] is False
    assert "trust_paused" in body["policy_refs"]
    snap = client.get("/state").json()
    assert snap["supplier_payments"] == []
    assert snap["spend"] == 0.0


def test_monitor_only_operator_records_spend_without_marking_it_paid(client):
    client.post("/trust_mode", json={"mode": "monitor_only"})
    op = BusinessOperator(state.agent, state.agent.stripe, spend_judge=state.spend_judge)
    job = Job(
        job_id="monitor-job",
        title="Monitoring job",
        revenue=100.0,
        protected_margin=40.0,
        customer_id="cus_monitor",
        invoice_ref="inv_monitor",
        tools=(ToolPurchase("compute", "compute", 20.0),),
    )

    outcome = op.run_job(job)

    assert outcome.spends[0].status == SpendStatus.RECORDED
    assert outcome.spends[0].paid is False
    snap = client.get("/state").json()
    assert snap["spend"] == 0.0
