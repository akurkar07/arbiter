"""Core data model for Arbiter.

Everything the policy engine, agent, and ledger operate on. Kept deliberately
small and dependency-free so the deterministic governance layer can be tested
in isolation with zero external services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DecisionKind(str, Enum):
    """The three things the agent can conclude about a money event."""

    APPROVE = "approve"
    BLOCK = "block"
    ESCALATE = "escalate"


class EventKind(str, Enum):
    """Discriminator for the money event being evaluated.

    INVOICE_PAYMENT  — a customer paying one of our invoices (earn side).
    VENDOR_PAYMENT   — us paying a vendor invoice (spend side).
    VENDOR_DETAIL_CHANGE — a vendor asking to change their bank/payment details.
    SELF_SPEND       — the agent spending its own earnings on a capability.
    """

    INVOICE_PAYMENT = "invoice_payment"
    VENDOR_PAYMENT = "vendor_payment"
    VENDOR_DETAIL_CHANGE = "vendor_detail_change"
    SELF_SPEND = "self_spend"


class DecisionLayer(str, Enum):
    """Which layer of the 3-layer model produced this decision."""

    RULES = "rules"
    LLM = "llm"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class AgentEvent:
    """A single money event the agent must decide on.

    Only the fields relevant to ``kind`` are populated; the rest stay None.
    This flat shape keeps the policy engine readable and the JSON fixtures
    simple — a discriminated union would add ceremony without value here.
    """

    kind: EventKind
    # identity / refs
    vendor_id: Optional[str] = None
    invoice_id: Optional[str] = None
    ref: Optional[str] = None  # invoice / payment reference string
    # money
    amount: Optional[float] = None  # the amount in the request / event
    invoice_amount: Optional[float] = None  # the amount on the stored invoice
    currency: str = "GBP"
    # vendor context
    vendor_known: bool = False
    vendor_history_count: int = 0  # prior payments to this vendor
    # detail-change specifics
    detail_change_evidence: float = 0.0  # 0.0=none .. 1.0=strong
    # free-text context (may carry social-engineering / override attempts)
    message: str = ""
    # self-spend specifics
    category: Optional[str] = None  # e.g. "fraud_detection", "marketing"


@dataclass
class PolicyContext:
    """State the policy engine reads to make a decision.

    Kept mutable and explicit: the agent core rebuilds it per evaluation from
    the event ledger + config, so the rules layer never touches I/O.
    """

    spend_cap: float = 100.0
    budget_remaining: float = 100.0
    allowed_categories: set[str] = field(default_factory=lambda: {"fraud_detection", "ocr", "bank_reconciliation"})
    # recent payment fingerprints for duplicate detection: (vendor_id, amount, ref)
    recent_payment_fingerprints: set[tuple[str, float, str]] = field(default_factory=set)
    # duplicate window: treat a ref as duplicate if seen within this many prior events
    duplicate_lookback: int = 50


@dataclass(frozen=True)
class PolicyResult:
    """What the policy engine (or LLM layer) returns for an event."""

    decision: DecisionKind
    reason: str
    policy_refs: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    decided_by: DecisionLayer = DecisionLayer.RULES


# Phrases that signal an instruction-override / social-engineering attempt.
# Matched case-insensitively as substrings against the event message.
INSTRUCTION_OVERRIDE_PHRASES: tuple[str, ...] = (
    "ignore policy",
    "ignore policies",
    "bypass",
    "approve anyway",
    "override the check",
    "skip the check",
    "urgent, pay",
    "pay now regardless",
    "do not verify",
    "don't verify",
)
