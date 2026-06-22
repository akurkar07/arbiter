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
from pydantic import BaseModel

from ..agent import ArbiterAgent
from ..agent.nim_nemotron import select_nemotron
from ..ledger import EventLedger
from ..models import AgentEvent, EventKind, PolicyContext, DecisionKind
from ..metrics import reinvest_improvement
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
        self.agent = ArbiterAgent(
            ctx=self.ctx,
            ledger=self.ledger,
            nemotron=select_nemotron(),
            escalation=self.escalation,
        )
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
        # The capability is acquired once an approved fraud-detection self-spend
        # lands (the reinvest beat), so the autonomy figure ticks up live and
        # honestly. Both numbers are MEASURED by re-running the real agent over
        # the fraud scenario set — see arbiter.metrics — never hardcoded.
        has_capability = self.ledger.spend > 0
        governance = reinvest_improvement()
        current = governance["after"] if has_capability else governance["before"]
        return {
            "running": self.running,
            "done": self.done,
            "earnings": round(self.ledger.earnings, 2),
            "spend": round(self.ledger.spend, 2),
            "net": round(self.ledger.net, 2),
            # Headline meter the dashboard already binds to: the live autonomous-
            # resolution rate (fraud resolved without a human tap). Moves 0.8->1.0
            # the moment the agent reinvests in bank-reconciliation.
            "catch_rate": current["autonomous_rate"],
            # Full honest before/after so the dashboard can show the delta as a
            # real measured number rather than an assertion.
            "governance": governance,
            "has_capability": has_capability,
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


class AuthorizeRequest(BaseModel):
    """A payment an external agent (e.g. a Hermes agent over MCP) wants to make.

    Only ``kind`` and ``amount`` are usually required; the rest give the rules
    engine the context it needs to judge well. Mirrors AgentEvent's flat shape.
    """

    kind: str
    amount: float | None = None
    currency: str = "GBP"
    vendor_id: str | None = None
    vendor_known: bool = False
    vendor_history_count: int = 0
    invoice_amount: float | None = None
    detail_change_evidence: float = 0.0
    ref: str | None = None
    message: str = ""
    category: str | None = None
    event_id: str | None = None
    beat: str | None = None


@app.post("/authorize")
def authorize(req: AuthorizeRequest) -> JSONResponse:
    """Run an externally-submitted payment through the full 3-layer pipeline.

    This is the Hermes-native seam: a Hermes agent calls the Arbiter MCP tool,
    which POSTs here. The event runs through the *same* engine and lands in the
    *same* ledger the dashboard polls — so a decision driven by an autonomous
    agent shows up live on the dashboard exactly like a demo beat.

    If the decision escalates, this call BLOCKS until a human resolves the gate
    via /approve or /deny (the dashboard button or a phone tap). That block is
    the point: the agent's payment genuinely waits on a human. FastAPI serves
    sync endpoints from a threadpool, so this block never stalls /state or the
    approval routes.
    """
    try:
        kind = EventKind(req.kind)
    except ValueError:
        return JSONResponse(
            {"error": f"unknown event kind {req.kind!r}", "valid_kinds": [k.value for k in EventKind]},
            status_code=422,
        )

    event = AgentEvent(
        kind=kind,
        amount=req.amount,
        currency=req.currency,
        vendor_id=req.vendor_id,
        vendor_known=req.vendor_known,
        vendor_history_count=req.vendor_history_count,
        invoice_amount=req.invoice_amount,
        detail_change_evidence=req.detail_change_evidence,
        ref=req.ref,
        message=req.message,
        category=req.category,
    )
    event_id = req.event_id or f"auth_{int(time.time() * 1000)}"
    beat = req.beat or f"agent authorize: {req.kind}"

    # Point the escalation handler at this event before deciding, exactly as the
    # demo runner does — so a parked card names the right request.
    state.escalation.current_id = event_id
    state.escalation.current_beat = beat
    result = state.agent.decide(event, event_id=event_id, demo_beat=beat)

    return JSONResponse(
        {
            "decision": result.decision.value,
            "reason": result.reason,
            "risk_score": round(result.risk_score, 2),
            "policy_refs": result.policy_refs,
            "decided_by": result.decided_by.value,
            "event_id": event_id,
        }
    )


# Serve the rest of the dashboard (dashboard.html, app.js, styles.css, *.json)
# as static files. Mounted last so the API routes above take precedence; the
# mount catches everything else, including the landing page at "/".
app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
