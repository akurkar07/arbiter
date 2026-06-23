"""Arbiter MCP server — the Hermes-native governance edge.

This exposes Arbiter's payment governance to any MCP client (a Hermes agent,
Claude Desktop, etc.) as callable tools. A Hermes agent running a business can
be handed a Stripe key and *cannot* move money without every payment passing
through Arbiter's three layers: deterministic rules -> bounded NVIDIA Nemotron
-> human approval gate.

Wire it into Hermes either way:

Quickest — the Hermes CLI writes the config for you::

    hermes mcp add arbiter --command python --args -m arbiter.mcp_server

Or add it by hand to ``mcp_servers`` in ~/.hermes/config.yaml::

    mcp_servers:
      arbiter:
        command: "python"
        args: ["-m", "arbiter.mcp_server"]
        env:
          ARBITER_BASE_URL: "http://127.0.0.1:8000"
        timeout: 300

Either way Hermes discovers the tools at startup and registers them as
``mcp_arbiter_*``. A ready-to-merge fragment lives at
``integration/hermes_mcp_servers.yaml``; the full story is in
``HERMES_INTEGRATION.md``.

Architecture note — why this proxies to the web server instead of running the
engine in-process: the demo dashboard and this MCP server are separate
processes, but the *story* needs them to share one ledger so an agent-driven
decision shows up live on the dashboard. So the MCP tools POST to the running
Arbiter web server (default http://127.0.0.1:8000). One engine, one ledger, two
front doors (the demo runner and the agent). If the web server is not running,
the tools say so rather than silently diverging.

Set ARBITER_BASE_URL to point at a non-default host/port.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("ARBITER_BASE_URL", "http://127.0.0.1:8000")
# Escalations block on a human; give them generous time before the client gives up.
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)

mcp = FastMCP("arbiter")


@mcp.tool()
def authorize_payment(
    kind: str,
    amount: float | None = None,
    currency: str = "GBP",
    vendor_id: str | None = None,
    vendor_known: bool = False,
    vendor_history_count: int = 0,
    invoice_amount: float | None = None,
    detail_change_evidence: float = 0.0,
    ref: str | None = None,
    message: str = "",
    category: str | None = None,
) -> dict:
    """Pay a supplier through Arbiter. This IS the payment — not a permission slip.

    You (the agent) do NOT hold the money rail; Arbiter does. This single call is
    the only way to move money: Arbiter runs the request through its governance
    pipeline and, if and only if it approves, executes the payment itself and
    returns the settlement receipt. There is no separate "now pay" step for you
    to take — and no way to pay around this. The returned ``decision`` tells you
    what happened:

      - "approve": Arbiter PAID the supplier. ``executed`` is true and
        ``stripe_id`` is the settlement handle (obp_test_... on the live rail).
      - "block":   nothing was paid. The payment violated policy; ``executed`` is
        false and no money moved. Do not try to route around it — there is no
        other door.
      - "escalate" resolves to approve/block AFTER a human owner decides — this
        call blocks until they tap approve or deny, then pays (or doesn't)
        accordingly. When it returns you have a real human decision and the money
        has already moved or been held, not a guess.

    Args:
        kind: one of "invoice_payment", "vendor_payment", "vendor_detail_change",
            "self_spend".
        amount: the amount to pay.
        currency: ISO currency code (default GBP).
        vendor_id: identifier of the payee.
        vendor_known: True if this is an established vendor.
        vendor_history_count: number of prior payments to this vendor.
        invoice_amount: the amount on the stored invoice, for mismatch checks.
        detail_change_evidence: 0.0-1.0 strength of evidence for a bank-detail
            change (only for vendor_detail_change).
        ref: invoice/payment reference string (used for duplicate detection).
        message: any free-text context attached to the request.
        category: spend category for self_spend (e.g. "fraud_detection").

    Returns:
        A dict with decision, reason, risk_score, policy_refs, decided_by, and
        the settlement truth: ``executed`` (did money actually move), ``stripe_id``
        (the rail handle when it did), and ``stripe_backend`` (real vs recorded).
    """
    payload = {
        "kind": kind,
        "amount": amount,
        "currency": currency,
        "vendor_id": vendor_id,
        "vendor_known": vendor_known,
        "vendor_history_count": vendor_history_count,
        "invoice_amount": invoice_amount,
        "detail_change_evidence": detail_change_evidence,
        "ref": ref,
        "message": message,
        "category": category,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{BASE_URL}/authorize", json=payload)
    except httpx.ConnectError:
        return {
            "error": "Arbiter governance server is not reachable.",
            "hint": f"Start it (uvicorn arbiter.web.server:app) and/or set ARBITER_BASE_URL. Tried {BASE_URL}.",
            "decision": "block",
            "reason": "Fail-safe: cannot authorize while the governance server is down, so the payment is blocked.",
        }
    if resp.status_code == 422:
        return resp.json()
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_policy_rules() -> dict:
    """List the governance rules Arbiter enforces, in priority order.

    Use this to explain to a user *why* a payment was approved, blocked, or
    escalated, or to show what controls are in force before acting.
    """
    from .policy.rules import _RULES

    rules = []
    for fn in _RULES:
        name = fn.__name__.lstrip("_")
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        rules.append({"rule": name, "summary": doc})
    return {"count": len(rules), "rules": rules, "order": "first match wins; no match -> escalate"}


@mcp.tool()
def get_ledger() -> dict:
    """Return the audit trail of every decision Arbiter has made this session.

    Pulls the live ledger from the running governance server so the agent (and
    its user) can see the full, timestamped record of what was approved,
    blocked, or escalated and why — the compliance/audit view.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{BASE_URL}/state")
        resp.raise_for_status()
    except httpx.ConnectError:
        return {
            "error": "Arbiter governance server is not reachable.",
            "hint": f"Start it and/or set ARBITER_BASE_URL. Tried {BASE_URL}.",
        }
    state = resp.json()
    return {
        "earnings": state.get("earnings"),
        "spend": state.get("spend"),
        "net": state.get("net"),
        "timeline": state.get("timeline", []),
        "awaiting_approval": state.get("awaiting_approval"),
    }


def main() -> None:
    """Entry point for `python -m arbiter.mcp_server` (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
