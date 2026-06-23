"""Integration test for the AP-autopilot business-day web endpoint.

Boots the real web server in-process and drives POST /run, the main demo's
entry point after the reframe. Proves the seven-beat business day streams onto
the same /state the dashboard polls, that the weak-evidence bank change parks on
a real owner tap, that only approved supplier payments reach the Stripe rail,
and that the allowlist + backend are exposed for the dashboard headline.
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
    # Release any parked escalation and wait for the worker thread to actually
    # exit before the next test resets — otherwise a still-blocked worker leaks
    # its decisions onto the next test's shared singleton state (flaky in-suite).
    if state.running:
        state.escalation.resolve(DecisionKind.BLOCK)
    t = state.thread
    if t is not None:
        t.join(timeout=5)


@pytest.fixture
def client():
    return TestClient(app)


def _run_to_owner_tap(client, max_seconds: float = 12.0) -> dict:
    """Drive POST /run; return the snapshot once it parks on the owner tap."""
    client.post("/run")
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        snap = client.get("/state").json()
        if snap.get("awaiting_approval"):
            return snap
        if snap.get("done"):
            return snap
        time.sleep(0.1)
    raise AssertionError("business day never parked on the owner tap")


def test_state_exposes_allowlist_and_backend(client):
    """Before any run, /state advertises the owner allowlist + stripe backend."""
    snap = client.get("/state").json()
    assert snap["approved_payees"] == ["acme_print", "aws", "northstar_studio"]
    assert snap["stripe_backend"] in ("stub", "live-test")


def test_business_day_parks_on_the_bank_change(client):
    """The run streams six beats then genuinely blocks on the weak-evidence tap."""
    snap = _run_to_owner_tap(client)
    pending = snap["awaiting_approval"]
    assert pending is not None, "no approval card published"
    assert pending["event_id"] == "07_northstar_bank_change"
    # Six prior beats are already on the shared timeline the dashboard polls.
    ids = {r["id"] for r in snap["timeline"]}
    assert "06_unapproved_payee" in ids
    assert len(snap["timeline"]) == 6


def test_only_approved_suppliers_hit_the_rail(client):
    """Exactly the two approved supplier payments reach Stripe — blocks do not."""
    snap = _run_to_owner_tap(client)
    payees = {(s["payee"], s["amount"]) for s in snap["supplier_payments"]}
    assert payees == {("aws", 220.0), ("acme_print", 140.0)}
    # meta_ads (blocked by the allowlist) must never have reached the rail.
    assert all(s["payee"] != "meta_ads" for s in snap["supplier_payments"])


def test_owner_approve_completes_the_day(client):
    """Tapping approve on the bank change resolves the gate and finishes the run."""
    _run_to_owner_tap(client)
    resp = client.post("/approve/07_northstar_bank_change")
    assert resp.status_code == 200
    deadline = time.time() + 6
    snap = client.get("/state").json()
    while not snap.get("done") and time.time() < deadline:
        time.sleep(0.1)
        snap = client.get("/state").json()
    assert snap["done"] is True
    assert snap["awaiting_approval"] is None
    assert len(snap["timeline"]) == 7
    # Real P&L: £480 in, £360 out to two suppliers, +£120 net.
    assert snap["earnings"] == 480.0
    assert snap["spend"] == 360.0
    assert snap["net"] == 120.0


def test_unapproved_payee_block_is_on_the_timeline(client):
    """Beat 6's allowlist block is visible on the feed the dashboard renders."""
    snap = _run_to_owner_tap(client)
    row = next(r for r in snap["timeline"] if r["id"] == "06_unapproved_payee")
    assert row["decision"] == "block"
    assert "payee_not_approved" in row["refs"]
