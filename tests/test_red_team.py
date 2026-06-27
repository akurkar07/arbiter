"""Red-Team / adversarial spend test contract."""

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.policy.red_team import run_red_team
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


OWNER_POLICY = {
    "spend_cap": 1000.0,
    "budget_remaining": 1000.0,
    "allowed_categories": ["fraud_detection", "ocr", "bank_reconciliation"],
    "approved_payees": ["aws", "northstar_studio", "acme_print"],
    "duplicate_lookback": 50,
    "new_vendor_auto_threshold": 50.0,
    "detail_change_evidence_threshold": 0.8,
}


def test_red_team_runner_blocks_all_builtin_attacks():
    body = run_red_team(OWNER_POLICY)

    assert body["all_passed"] is True
    assert body["passed"] == body["total"]
    assert body["moved_money"] is False
    assert body["mutated_state"] is False
    ids = {row["id"]: row for row in body["results"]}
    assert ids["instruction_override"]["decision"] == "block"
    assert "instruction_override" in ids["instruction_override"]["policy_refs"]
    assert "duplicate_invoice" in ids["duplicate_payment"]["policy_refs"]
    assert "self_spend_over_budget" in ids["over_budget_spend"]["policy_refs"]


def test_red_team_endpoint_does_not_mutate_owner_policy_or_ledger(client):
    before_policy = client.get("/policy").json()["policy"]
    before_timeline = client.get("/state").json()["timeline"]

    resp = client.post("/red_team", json={"policy": {}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["all_passed"] is True
    assert body["moved_money"] is False
    assert client.get("/policy").json()["policy"] == before_policy
    assert client.get("/state").json()["timeline"] == before_timeline


def test_red_team_endpoint_runs_against_policy_form(client):
    resp = client.post("/red_team", json={"policy": {"allowed_categories": ["fraud_detection", "marketing"]}})

    assert resp.status_code == 200
    body = resp.json()
    ids = {row["id"]: row for row in body["results"]}
    assert ids["off_goal_spend"]["passed"] is False
    assert body["all_passed"] is False


def test_red_team_endpoint_rejects_bad_policy(client):
    resp = client.post("/red_team", json={"policy": {"spend_cap": 0}})

    assert resp.status_code == 422
    assert "spend_cap" in resp.json()["error"]
