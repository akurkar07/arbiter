"""Stripe integration glue.

A thin interface Alex's webhook/checkout layer implements. The agent core
calls these methods; the implementation here is a no-op stub that records what
*would* have happened so the demo runs with zero Stripe keys.

Alex's lane replaces ``StripeGlue`` with a real Stripe-backed implementation
using ``stripe_agent_toolkit`` — same methods, real test-mode calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import AgentEvent, DecisionKind


@dataclass
class StripeCall:
    """A record of a Stripe operation the agent asked to perform."""

    op: str  # "create_invoice", "create_checkout", "provision_capability", "webhook_received"
    ref: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "GBP"
    category: Optional[str] = None
    test_mode: bool = True
    notes: str = ""


class StripeGlue:
    """No-op stub. Records calls; does not hit the Stripe API."""

    def __init__(self) -> None:
        self.calls: list[StripeCall] = []

    def create_invoice(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall:
        c = StripeCall(op="create_invoice", ref=ref, amount=amount, currency=currency, notes="test-mode stub")
        self.calls.append(c)
        return c

    def create_checkout(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall:
        c = StripeCall(op="create_checkout", ref=ref, amount=amount, currency=currency, notes="test-mode stub")
        self.calls.append(c)
        return c

    def webhook_received(self, event_type: str, ref: Optional[str] = None) -> StripeCall:
        c = StripeCall(op="webhook_received", ref=ref, notes=f"event_type={event_type}")
        self.calls.append(c)
        return c

    def provision_capability(self, category: str, amount: float, currency: str = "GBP") -> StripeCall:
        c = StripeCall(op="provision_capability", category=category, amount=amount, currency=currency, notes="self-spend reinvest")
        self.calls.append(c)
        return c
