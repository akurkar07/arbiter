"""Arbiter demo web server.

Serves the live demo the judges watch:

  GET  /state            -> the whole demo state as one JSON object (dashboard polls this)
  POST /run              -> start playing the scenario timeline
  POST /reset            -> rebuild a fresh agent/ledger so the demo can replay
  GET/POST /approve/{id} -> owner approves a parked escalation
  GET/POST /deny/{id}    -> owner denies a parked escalation

The escalation is the point. When the agent defers an ambiguous money decision,
the demo thread genuinely *blocks* until a real owner tap resolves it — the
human-in-the-loop trust beat is real here, not auto-approved. A phone hitting the
approve link (a GET) resolves the same gate the dashboard button (a POST) does.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..agent import ArbiterAgent
from ..ledger import EventLedger
from ..models import EventKind, PolicyContext, DecisionKind
from ..reinvest import fraud_catch_rate
from ..scenarios import list_scenarios, load_scenario
from ..stripe_glue import StripeGlue

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"


class WebEscalation:
    """Escalation handler that parks the decision for a real owner tap.

    Implements the EscalationHandler protocol. Instead of auto-approving, it
    publishes the pending event through ``pending`` (which /state exposes) and
    blocks the calling thread on a gate until /approve or /deny releases it.
    That blocking is deliberate: it makes the "ask the owner" beat a real pause,
    so the dashboard genuinely waits on a human the way the product would.
    """

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._decision: DecisionKind | None = None
        self.pending: dict | None = None
        # Set by the runner immediately before each decide() so the parked
        # card can name the event the rules layer is deferring on.
        self.current_id: str = ""
        self.current_beat: str = ""

    def request_approval(self, event, result) -> DecisionKind:
        self.pending = {
            "event_id": self.current_id,
            "kind": event.kind.value,
            "beat": self.current_beat,
            "reason": result.reason,
            "risk": round(result.risk_score, 2),
            "amount": event.amount,
            "currency": event.currency,
            "approve_url": f"/approve/{self.current_id}",
            "deny_url": f"/deny/{self.current_id}",
        }
        self._gate.clear()
        self._decision = None
        self._gate.wait()  # block here until the owner acts
        self.pending = None
        # Default to BLOCK if somehow released without a decision: safe-by-default.
        return self._decision or DecisionKind.BLOCK

    def resolve(self, decision: DecisionKind) -> None:
        self._decision = decision
        self._gate.set()


class DemoState:
    """Holds one playthrough of the demo timeline behind the web endpoints."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.ctx = PolicyContext()
        self.ledger = EventLedger()
        self.escalation = WebEscalation()
        self.agent = ArbiterAgent(ctx=self.ctx, ledger=self.ledger, escalation=self.escalation)
        self.stripe = StripeGlue()
        self.running = False
        self.done = False
        self.thread: threading.Thread | None = None

    def _run(self, step_delay: float) -> None:
        for name in list_scenarios():
            event, _expected, raw = load_scenario(name)
            beat = raw.get("demo_beat", name)

            # Seed any pre-demo payment fingerprints (for duplicate detection).
            for fp in raw.get("seed_fingerprints", []):
                self.ctx.recent_payment_fingerprints.add((fp[0], fp[1], fp[2]))

            # Earn beat: a customer paying an invoice arrives via a Stripe webhook.
            if event.kind == EventKind.INVOICE_PAYMENT:
                self.stripe.create_checkout(event.ref or "n/a", event.amount or 0, event.currency)
                self.stripe.webhook_received("checkout.session.completed", event.ref)

            # Tell the escalation handler which event it's about to be asked about.
            self.escalation.current_id = name
            self.escalation.current_beat = beat

            self.agent.decide(event, event_id=name, demo_beat=beat)
            time.sleep(step_delay)

        self.done = True
        self.running = False

    def start(self, step_delay: float = 1.2) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.done = False
            self.thread = threading.Thread(target=self._run, args=(step_delay,), daemon=True)
            self.thread.start()

    def snapshot(self) -> dict:
        # capability is acquired once an approved fraud-detection self-spend lands,
        # which is the reinvest beat — so the catch-rate ticks up live and honestly.
        has_capability = self.ledger.spend > 0
        return {
            "running": self.running,
            "done": self.done,
            "earnings": round(self.ledger.earnings, 2),
            "spend": round(self.ledger.spend, 2),
            "net": round(self.ledger.net, 2),
            "catch_rate": fraud_catch_rate(has_capability),
            "awaiting_approval": self.escalation.pending,
            # insertion order matches dashboard/sample_state.json; the UI reverses
            # for display so the contract fixture and the live feed are identical.
            "timeline": self.ledger.as_timeline(),
        }


app = FastAPI(title="Arbiter", docs_url=None, redoc_url=None)
state = DemoState()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/state")
def get_state() -> JSONResponse:
    return JSONResponse(state.snapshot())


@app.post("/run")
def run() -> dict:
    state.start()
    return {"running": True}


@app.post("/reset")
def reset() -> dict:
    with state.lock:
        if state.running:
            # release any parked escalation so the worker thread can exit
            state.escalation.resolve(DecisionKind.BLOCK)
        state.reset()
    return {"reset": True}


def _resolve(event_id: str, decision: DecisionKind) -> HTMLResponse:
    state.escalation.resolve(decision)
    label = "Approved" if decision == DecisionKind.APPROVE else "Denied"
    # A phone opens the link in a browser; give it a clean confirmation page.
    return HTMLResponse(
        f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<body style='font-family:system-ui;background:#0b0f17;color:#e6edf3;"
        f"display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='text-align:center'><div style='font-size:64px'>"
        f"{'&#10003;' if decision == DecisionKind.APPROVE else '&#10007;'}</div>"
        f"<h2>{label}</h2><p style='color:#8b949e'>{event_id}</p>"
        f"<p style='color:#8b949e'>You can return to the dashboard.</p></div></body>"
    )


@app.api_route("/approve/{event_id}", methods=["GET", "POST"])
def approve(event_id: str) -> HTMLResponse:
    return _resolve(event_id, DecisionKind.APPROVE)


@app.api_route("/deny/{event_id}", methods=["GET", "POST"])
def deny(event_id: str) -> HTMLResponse:
    return _resolve(event_id, DecisionKind.BLOCK)


# Serve the rest of the dashboard (dashboard.html, app.js, styles.css, *.json)
# as static files. Mounted last so the API routes above take precedence; the
# mount catches everything else, including the landing page at "/".
app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
