# Alex — Handoff & Your Interface

**Repo:** this repository (Ben pushes to GitHub and adds you as collaborator)
**Hackathon:** Nous × NVIDIA × Stripe — deadline EOD June 30, 2026
**Team:** Ben (agent core + governance, orchestrate, present) · Alex (Stripe + dashboard + phone UI)

---

## What's already built (core — DONE)

The entire governance core + agent loop + demo runner is built, tested, and committed:

```
arbiter/
  models.py             # AgentEvent, PolicyContext, PolicyResult, DecisionKind
  policy/rules.py       # 10 deterministic rules (the moat)
  agent/
    agent.py            # 3-layer core: rules -> Nemotron -> phone escalation
    nemotron.py         # bounded LLM layer (MockNemotron, strict JSON, no network)
    escalation.py       # phone escalation (ConsoleEscalation for demo)
  ledger/event_ledger.py  # append-only ledger, dashboard-ready timeline
  reinvest.py           # self-funded reinvest + fraud catch-rate metric
  stripe_glue.py        # thin interface — YOUR webhook layer implements this
  scenarios.py          # loads JSON fixtures -> AgentEvent
  cli.py                # demo runner (python -m arbiter.cli)
scenarios/*.json        # 10 fixtures covering the full demo storyboard
tests/test_policy_engine.py  # 13 pytest, all passing
```

**Proof it works:**
```
pytest: 13 passed in 0.02s
demo CLI: full timeline plays — earn, 4 blocks, 2 escalates, 2 self-blocks, reinvest, improve
```

Run it yourself:
```bash
cd arbiter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # 13 passed
python -m arbiter.cli # full demo timeline
```

---

## Your interface — what you implement

### 1. StripeGlue (arbiter/stripe_glue.py)

The agent core calls these methods. The current implementation is a no-op stub
that records calls. You replace it with real Stripe test-mode calls using
`stripe_agent_toolkit`:

```python
class StripeGlue:
    def create_invoice(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall: ...
    def create_checkout(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall: ...
    def webhook_received(self, event_type: str, ref: str | None = None) -> StripeCall: ...
    def provision_capability(self, category: str, amount: float, currency: str = "GBP") -> StripeCall: ...
```

**Your job:** make these hit Stripe test mode. The agent core doesn't care about
the Stripe details — it calls `create_checkout` + `webhook_received` for the earn
beat, and `provision_capability` for the reinvest beat. You wire the real API.

**Webhook receiver:** build a small Flask/FastAPI endpoint that receives
`checkout.session.completed` and calls into the agent core with an
`AgentEvent(kind=INVOICE_PAYMENT, ...)`.

### 2. Dashboard (dashboard/)

The ledger exposes a dashboard-ready timeline:
```python
from arbiter.ledger import EventLedger
ledger.as_timeline()  # list[dict] with: t, id, kind, decision, reason, refs, risk, layer, amount, beat
```

Build a simple web UI that shows the timeline in real time during the demo.
Each entry has a `beat` field with the human-readable story line.

### 3. Phone escalation UI

Replace `ConsoleEscalation` with a real mobile approval UI:
```python
from arbiter.agent import EscalationHandler

class PhoneEscalation(EscalationHandler):
    def request_approval(self, event, result) -> DecisionKind:
        # push to phone, wait for y/n tap
        ...
```

For the demo, a simple Twilio SMS or push notification with a tap-to-approve
link is enough. The agent core just needs a yes/no back.

### 4. Scenario generator (optional polish)

The 10 JSON fixtures in `scenarios/` cover the demo. If you want to add more
edge cases, the format is:
```json
{
  "id": "...",
  "kind": "invoice_payment|vendor_payment|vendor_detail_change|self_spend",
  "vendor_id": "...", "amount": 480.00, "invoice_amount": 480.00,
  "vendor_known": true, "vendor_history_count": 6,
  "message": "", "category": "fraud_detection",
  "expected_decision": "approve|block|escalate",
  "demo_beat": "one-line story for the dashboard"
}
```

---

## What you do NOT need to touch

- `policy/rules.py` — the deterministic engine. Done, tested, it's the moat.
- `agent/agent.py` — the 3-layer core. Done.
- `agent/nemotron.py` — the bounded LLM layer. MockNemotron works for the demo;
  the real Nemotron NIM call gets wired in when the key is ready.
- `ledger/event_ledger.py` — done.
- `reinvest.py` — done.
- `cli.py` — demo runner. Done.

---

## Integration point

When your Stripe webhook fires, call the agent:
```python
from arbiter.agent import ArbiterAgent
from arbiter.models import AgentEvent, EventKind, PolicyContext

agent = ArbiterAgent(ctx=PolicyContext())
event = AgentEvent(kind=EventKind.INVOICE_PAYMENT, vendor_id="cust_x", ...)
result = agent.decide(event, event_id="evt_001", demo_beat="Customer paid invoice")
# result.decision == DecisionKind.APPROVE | BLOCK | ESCALATE
# ledger already recorded it
```

---

## Blocking items (repo admin)

1. **Create the GitHub repo** and push this repository to it. — done when you can read this on GitHub
2. **Add Alex as collaborator** on the repo.
3. **Share the Google Drive** folder with Alex (hackathon plans + v3 docs + fixtures).
4. **Stripe test keys** — put `STRIPE_SECRET_KEY` (test mode, `sk_test_...`) and `STRIPE_WEBHOOK_SECRET` in `.env`. No real money.

Once the repo is up, Alex clones and starts on StripeGlue + dashboard. The real
Nemotron NIM call gets wired in parallel.
