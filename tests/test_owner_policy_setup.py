"""Owner Policy Setup backend contract.

The setup UI is only meaningful if the backend stores validated owner rules and
the policy engine actually follows them.
"""

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.policy.config import PolicyConfigError, policy_context_from_dict
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


def test_policy_endpoint_exposes_owner_rules(client):
    body = client.get("/policy").json()
    assert body["running"] is False
    assert body["policy"]["spend_cap"] == 1000.0
    assert body["policy"]["approved_payees"] == ["aws", "northstar_studio", "acme_print"]


def test_policy_update_changes_decision_path(client):
    resp = client.post("/policy", json={"approved_payees": ["acme_print"]})
    assert resp.status_code == 200
    assert resp.json()["policy"]["approved_payees"] == ["acme_print"]

    blocked = client.post("/authorize", json={
        "kind": "vendor_payment",
        "vendor_id": "aws",
        "amount": 42.0,
        "invoice_amount": 42.0,
        "vendor_known": True,
        "vendor_history_count": 12,
        "ref": "policy-aws-blocked",
    }).json()
    assert blocked["decision"] == "block"
    assert "payee_not_approved" in blocked["policy_refs"]


def test_policy_partial_update_preserves_unspecified_rules(client):
    resp = client.post("/policy", json={"spend_cap": 250.0})
    assert resp.status_code == 200
    policy = resp.json()["policy"]
    assert policy["spend_cap"] == 250.0
    assert policy["approved_payees"] == ["acme_print", "aws", "northstar_studio"]
    assert policy["allowed_categories"] == ["bank_reconciliation", "fraud_detection", "ocr"]


def test_policy_update_rejects_bad_values(client):
    resp = client.post("/policy", json={"detail_change_evidence_threshold": 1.5})
    assert resp.status_code == 422
    assert "detail_change_evidence_threshold" in resp.json()["error"]


def test_policy_update_rejects_unknown_keys(client):
    resp = client.post("/policy", json={"spendcap": 500})
    assert resp.status_code == 422


def test_policy_update_rejected_while_run_active(client):
    state.running = True
    try:
        resp = client.post("/policy", json={"spend_cap": 300.0})
    finally:
        state.running = False
    assert resp.status_code == 409


def test_policy_config_rejects_bool_numbers():
    with pytest.raises(PolicyConfigError, match="spend_cap"):
        policy_context_from_dict({"spend_cap": True})


def test_policy_config_loads_duplicate_lookback():
    ctx = policy_context_from_dict({"duplicate_lookback": 12})
    assert ctx.duplicate_lookback == 12


def test_policy_config_rejects_bad_duplicate_lookback():
    with pytest.raises(PolicyConfigError, match="duplicate_lookback"):
        policy_context_from_dict({"duplicate_lookback": 0})
