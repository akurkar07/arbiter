# Arbiter × Hermes Agent — the native integration

Arbiter is the **payment-governance layer a Hermes agent wears when you hand it
a Stripe key.** A Hermes agent running someone's accounts-payable can be told
"pay the suppliers when revenue lands" — but with Arbiter in front of the money,
it *physically cannot* pay anyone the owner didn't approve, double-pay an
invoice, overpay, or move money on a weak-evidence bank change without a human
tap. The agent stays autonomous; the money stays governed.

This is the hackathon's "Hermes Agent" requirement satisfied at the seam, not
bolted on: Arbiter registers as a native MCP server, and every payment a Hermes
agent wants to make flows through Arbiter's three layers before a pound moves.

## How it plugs in

Hermes discovers MCP servers from `mcp_servers` in `~/.hermes/config.yaml` at
startup and registers their tools as first-class agent tools (prefixed
`mcp_arbiter_*`). Register Arbiter either with the Hermes CLI:

```bash
hermes mcp add arbiter --command python --args -m arbiter.mcp_server
```

or by dropping this into `~/.hermes/config.yaml` by hand:

```yaml
mcp_servers:
  arbiter:
    command: "python"
    args: ["-m", "arbiter.mcp_server"]
    env:
      # Point the tools at the running governance server (one engine, one ledger).
      ARBITER_BASE_URL: "http://127.0.0.1:8000"
    timeout: 300          # escalations block on a human; give them room
    connect_timeout: 30
```

Then start the governance server (the dashboard + engine + shared ledger) and
restart Hermes:

```bash
# 1. the governance server — the engine, the ledger, the human-approval gate
uvicorn arbiter.web.server:app --host 127.0.0.1 --port 8000

# 2. restart Hermes; it spawns `python -m arbiter.mcp_server` over stdio,
#    discovers the tools, and injects them into every conversation
```

A ready-to-merge config fragment lives at
[`integration/hermes_mcp_servers.yaml`](integration/hermes_mcp_servers.yaml).

## The three tools a Hermes agent gets

| Tool | What the agent uses it for |
|------|----------------------------|
| `mcp_arbiter_authorize_payment` | **Call before moving any money.** Returns `approve` / `block` / `escalate`. On `escalate` the call *blocks* until a human taps approve/deny, so the return value is a real decision, not a guess. |
| `mcp_arbiter_list_policy_rules` | Introspect the controls in force (priority order) — so the agent can explain *why* a payment was approved or blocked. |
| `mcp_arbiter_get_ledger` | Pull the timestamped audit trail of every decision this session — the compliance view. |

## Why it proxies to the web server (one engine, one ledger)

The MCP server is a thin client: its tools POST to the running Arbiter web
server rather than running the engine in-process. That's deliberate — the demo
dashboard and the Hermes agent are separate processes, but the *story* needs
them to share one ledger, so an agent-driven decision shows up **live on the
dashboard** exactly like a demo beat. One engine, one ledger, two front doors
(the demo runner and the agent). If the governance server is down,
`authorize_payment` **fails closed** — it returns `block`, never a silent pass.

## The governance a Hermes agent inherits (proven)

The moment a Hermes agent is handed Arbiter, these are enforced on every
`authorize_payment` call — verified end-to-end through the MCP → `/authorize` →
engine → ledger path in `tests/test_authorize_endpoint.py`:

- **Off-list payee → BLOCK.** The agent cannot pay a supplier the owner never
  approved, even though the agent itself drove the request
  (`policy_refs: ['payee_not_approved']`).
- **Approved, established supplier, reconciled amount → APPROVE.** Autopilot
  pays — no human needed.
- **Duplicate / overpay → BLOCK.** Fail-closed money controls.
- **Weak-evidence bank change → ESCALATE.** The call blocks until the owner taps
  approve/deny on their phone; the agent waits on a real human.

That is the pitch in one line: **a Hermes agent you can hand a Stripe key,
because it can't move money it shouldn't.**
