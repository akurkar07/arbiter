"""Integration test for the business-operator web endpoint.

Boots the real web server in-process and drives POST /run_operator, the swing
demo's entry point. Proves the operator's decisions stream onto the SAME /state
the dashboard polls, that a margin-killing spend parks on the human gate (the
phone beat), and that the business rollup the dashboard renders is exposed and
shows every margin protected.
"""

import time

import pytest
from fastapi.testclient import TestClient

from arbiter.models import DecisionKind
from arbiter.web.server import app, state


@pytest.fixture(autouse=True)
def _fresh_state():
    state.reset()
    yield
    if state.running:
        state.escalation.resolve(DecisionKind.BLOCK)


@pytest.fixture
def client():
    return TestClient(app)


def _drain_operator(client, max_seconds: float = 10.0) -> dict:
    """Run the operator to completion, tapping approve on every parked refusal."""
    client.post("/run_operator", json={})
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        snap = client.get("/state").json()
        pending = snap.get("awaiting_approval")
        if pending:
            # Owner taps approve on the refusal acknowledgement so the run proceeds.
            client.get(pending["approve_url"])
            continue
        if snap.get("done"):
            return snap
        time.sleep(0.05)
    raise AssertionError("operator run did not finish in time")


def test_run_operator_exposes_business_rollup(client):
    snap = _drain_operator(client)
    assert snap["operator_mode"] is True
    business = snap["business"]
    assert business is not None
    # Real money math surfaced to the dashboard.
    assert business["jobs_total"] == 4
    assert business["jobs_completed"] == 3            # one fraud invoice rejected
    assert business["revenue_booked"] == 350.0
    assert business["cost_spent"] == 85.0
    assert business["waste_blocked"] == 60.0
    assert business["fraud_revenue_rejected"] == 200.0
    assert business["net_profit"] == 265.0
    assert business["balance"] == 315.0
    # The invariant the whole pitch rests on.
    assert business["all_margins_protected"] is True


def test_operator_decisions_land_on_shared_timeline(client):
    snap = _drain_operator(client)
    timeline = snap["timeline"]
    # Every booked invoice + every attempted spend is on the same feed the
    # dashboard already renders. 3 invoices booked + 1 rejected + 5 spends.
    ids = [r["id"] for r in timeline]
    assert any(i.endswith(":invoice") for i in ids)
    assert any(":spend:" in i for i in ids)
    # The signature beat is present and is a block on a self_spend.
    margin_block = [
        r for r in timeline
        if r["kind"] == "self_spend"
        and r["decision"] == "block"
        and "self_spend_over_budget" in r["refs"]
    ]
    assert margin_block, "the margin-refusal beat is missing from the timeline"


def test_margin_refusal_parks_on_human_gate(client):
    """A refused spend must genuinely pause for an owner tap (the phone beat)."""
    client.post("/run_operator", json={})
    # Within a couple of seconds a refusal should publish a pending approval card.
    pending_id = None
    for _ in range(100):
        time.sleep(0.05)
        pending = client.get("/state").json().get("awaiting_approval")
        if pending:
            pending_id = pending["event_id"]
            break
    assert pending_id, "operator never parked a refusal on the human gate"
    assert "refused" in pending_id
    # Release it so the worker can finish and the test exits cleanly.
    client.get(f"/approve/{pending_id}")
