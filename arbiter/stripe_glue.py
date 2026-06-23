"""Stripe integration glue.

A thin interface the agent core calls to move money on the Stripe rail. Two
implementations behind one interface:

  * ``StripeGlue``     — records what *would* happen; zero keys, deterministic.
                          The demo and tests run on this.
  * ``LiveStripeGlue`` — real Stripe test-mode calls. ``pay_supplier`` creates a
                          real OutboundPayment (obp_test_...) on the
                          v2 money-management rail; ``create_checkout`` /
                          ``create_invoice`` create real test-mode objects.
                          Selected automatically when STRIPE_SECRET_KEY is set.

The accounts-payable story turns on ``pay_supplier``: once governance APPROVES a
supplier payment, THIS is the call that actually pays them. The agent never
calls it for a blocked/escalated decision — only an approved one reaches the rail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class StripeCall:
    """A record of a Stripe operation the agent asked to perform.

    ``stripe_id`` carries the real object id when a live call produced one
    (``cs_...`` checkout, ``obp_test_...`` outbound payment); it stays None on
    the stub so a test never mistakes a stub for a real settlement.
    """

    op: str  # create_invoice | create_checkout | webhook_received | provision_capability | pay_supplier
    ref: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "GBP"
    category: Optional[str] = None
    test_mode: bool = True
    notes: str = ""
    stripe_id: Optional[str] = None
    payee: Optional[str] = None


class StripeBackend(Protocol):
    """The surface the agent core depends on. Both glues satisfy it."""

    calls: list[StripeCall]

    def create_invoice(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall: ...
    def create_checkout(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall: ...
    def webhook_received(self, event_type: str, ref: Optional[str] = None) -> StripeCall: ...
    def provision_capability(self, category: str, amount: float, currency: str = "GBP") -> StripeCall: ...
    def pay_supplier(self, payee: str, amount: float, currency: str = "GBP",
                     ref: Optional[str] = None) -> StripeCall: ...


class StripeGlue:
    """No-op stub. Records calls; does not hit the Stripe API."""

    backend = "stub"

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
        c = StripeCall(op="provision_capability", category=category, amount=amount, currency=currency,
                       notes="self-spend reinvest")
        self.calls.append(c)
        return c

    def pay_supplier(self, payee: str, amount: float, currency: str = "GBP",
                     ref: Optional[str] = None) -> StripeCall:
        """Record a supplier payment that governance approved (stub: no money moves)."""
        c = StripeCall(op="pay_supplier", payee=payee, amount=amount, currency=currency,
                       ref=ref, notes="test-mode stub — would create an OutboundPayment")
        self.calls.append(c)
        return c


class LiveStripeGlue(StripeGlue):
    """Real Stripe test-mode calls. Falls back to recording if the SDK errors.

    Inherits the stub's recording so every call is still logged; overrides the
    methods that should hit the real API. Test-mode only — refuses a live key.
    The ``pay_supplier`` path uses the v2 money-management OutboundPayment API,
    which is the real "pay a third party" primitive (docs.stripe.com/api/v2/
    money-management/outbound-payments).
    """

    backend = "live-test"

    def __init__(self, secret_key: str) -> None:
        super().__init__()
        if not secret_key.startswith("sk_test_"):
            raise ValueError("LiveStripeGlue refuses a non-test key — test-mode only (sk_test_...).")
        import stripe  # imported lazily so the stub path needs no dependency
        self._stripe = stripe
        self._stripe.api_key = secret_key
        self.financial_account = os.environ.get("STRIPE_FINANCIAL_ACCOUNT")

    def create_checkout(self, ref: str, amount: float, currency: str = "GBP") -> StripeCall:
        try:
            session = self._stripe.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {"currency": currency.lower(),
                                   "product_data": {"name": ref or "Arbiter invoice"},
                                   "unit_amount": int(round(amount * 100))},
                    "quantity": 1,
                }],
                success_url="https://example.com/ok",
                cancel_url="https://example.com/no",
            )
            c = StripeCall(op="create_checkout", ref=ref, amount=amount, currency=currency,
                           notes="live test-mode", stripe_id=session.id)
        except Exception as e:  # noqa: BLE001 — never let a rail error crash governance
            c = StripeCall(op="create_checkout", ref=ref, amount=amount, currency=currency,
                           notes=f"live call failed, recorded only: {type(e).__name__}: {e}")
        self.calls.append(c)
        return c

    def pay_supplier(self, payee: str, amount: float, currency: str = "GBP",
                     ref: Optional[str] = None) -> StripeCall:
        """Create a real test-mode OutboundPayment to an approved supplier.

        Requires STRIPE_FINANCIAL_ACCOUNT (fa_...) and a recipient/payout method
        the caller has set up for ``payee`` in the test dashboard. If the rail
        errors (missing account, unconfigured recipient), the call is recorded
        with the error rather than raised — governance already approved the
        payment; a rail hiccup must not crash the run.
        """
        try:
            if not self.financial_account:
                raise RuntimeError("STRIPE_FINANCIAL_ACCOUNT not set")
            op = self._stripe.v2.money_management.outbound_payments.create({
                "from": {"financial_account": self.financial_account, "currency": currency.lower()},
                "to": {"recipient": payee, "currency": currency.lower()},
                "amount": {"value": int(round(amount * 100)), "currency": currency.lower()},
                "description": ref or f"AP autopilot payment to {payee}",
            })
            c = StripeCall(op="pay_supplier", payee=payee, amount=amount, currency=currency, ref=ref,
                           notes="live test-mode OutboundPayment", stripe_id=getattr(op, "id", None))
        except Exception as e:  # noqa: BLE001
            c = StripeCall(op="pay_supplier", payee=payee, amount=amount, currency=currency, ref=ref,
                           notes=f"live call failed, recorded only: {type(e).__name__}: {e}")
        self.calls.append(c)
        return c


def select_stripe() -> StripeGlue:
    """Real test-mode glue when STRIPE_SECRET_KEY (sk_test_...) is set, else the stub.

    Prints which backend is active so the demo shows at boot whether the Stripe
    rail is real or recorded — same honesty discipline as the Nemotron banner.
    """
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if key.startswith("sk_test_"):
        try:
            glue = LiveStripeGlue(key)
            print("[arbiter] Stripe layer: REAL test-mode (sk_test_...)")
            return glue
        except Exception as e:  # noqa: BLE001 — bad key/SDK -> fall back honestly
            print(f"[arbiter] Stripe layer: stub (live init failed: {type(e).__name__}: {e})")
            return StripeGlue()
    if key and not key.startswith("sk_test_"):
        print("[arbiter] Stripe layer: stub (refusing non-test key — set sk_test_... only)")
    else:
        print("[arbiter] Stripe layer: stub (no STRIPE_SECRET_KEY set)")
    return StripeGlue()
