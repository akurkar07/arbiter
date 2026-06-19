# Alex — Handoff & Your Interface

**Repo:** `/root/helios-workspace/ledgerguard/` (local; Ben will push to GitHub and add you)
**Hackathon:** Nous × NVIDIA × Stripe — deadline EOD June 30, 2026
**Team:** Ben (orchestrate + present) · Alex (Stripe + dashboard + phone UI) · Helios (agent core + governance)

---

## What's already built (Helios's lane — DONE)

The entire governance core + agent loop + demo runner is built, tested, and committed:

```
ledgerguard/
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
  cli.py                # demo runner (python -m ledgerguard.cli)
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
cd /root/helios-workspace/ledgerguard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # 13 passed
python -m ledgerguard.cli # full demo timeline
```

---

## Your interface — what you implement

### 1. StripeGlue (ledgerguard/stripe_glue.py)

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
from ledgerguard.ledger import EventLedger
ledger.as_timeline()  # list[dict] with: t, id, kind, decision, reason, refs, risk, layer, amount, beat
```

Build a simple web UI that shows the timeline in real time during the demo.
Each entry has a `beat` field with the human-readable story line.

### 3. Phone escalation UI

Replace `ConsoleEscalation` with a real mobile approval UI:
```python
from ledgerguard.agent import EscalationHandler

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
  Helios will wire the real Nemotron NIM call when the key is ready.
- `ledger/event_ledger.py` — done.
- `reinvest.py` — done.
- `cli.py` — demo runner. Done.

---

## Integration point

When your Stripe webhook fires, call the agent:
```python
from ledgerguard.agent import LedgerGuardAgent
from ledgerguard.models import AgentEvent, EventKind, PolicyContext

agent = LedgerGuardAgent(ctx=PolicyContext())
event = AgentEvent(kind=EventKind.INVOICE_PAYMENT, vendor_id="cust_x", ...)
result = agent.decide(event, event_id="evt_001", demo_beat="Customer paid invoice")
# result.decision == DecisionKind.APPROVE | BLOCK | ESCALATE
# ledger already recorded it
```

---

## Ben's blocking items (only he can do these)

1. **Create the GitHub repo** and push `/root/helios-workspace/ledgerguard/` to it.
2. **Add Alex as collaborator** on the repo.
3. **Share the Google Drive** folder with Alex (plans are in `/root/helios-workspace/hackathons/nous-nvidia-stripe-2026/`).
4. **Stripe test keys** — put `STRIPE_SECRET_KEY` (test mode, `sk_test_...`) and `STRIPE_WEBHOOK_SECRET` in `.env`. No real money.

Once Ben does #1, Alex clones and starts on StripeGlue + dashboard. Helios wires
the real Nemotron NIM call in parallel.
