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
    # Owner-approved supplier allowlist: the agent may only pay vendors whose id
    # is in this set. None = allowlist not configured (control inert, other rules
    # still apply). A configured set is enforced strictly — an empty set approves
    # no payee (fail closed). This is the foundational "can't pay anyone you
    # didn't approve" guarantee.
    approved_payees: Optional[set[str]] = None
    # recent payment fingerprints for duplicate detection: (vendor_id, amount, ref)
    recent_payment_fingerprints: set[tuple[str, float, str]] = field(default_factory=set)
    # duplicate window: treat a ref as duplicate if seen within this many prior events
    duplicate_lookback: int = 50
    # --- tunable rule thresholds (policy-as-config) ---
    new_vendor_auto_threshold: float = 50.0
    detail_change_evidence_threshold: float = 0.8


@dataclass(frozen=True)
class PolicyResult:
    """What the policy engine (or LLM layer) returns for an event."""

    decision: DecisionKind
    reason: str
    policy_refs: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    decided_by: DecisionLayer = DecisionLayer.RULES


@dataclass(frozen=True)
class SettlementResult:
    """The result of asking Arbiter to *settle* a payment: decide AND execute.

    This is what the single money door returns. It carries the full governance
    decision plus the rail receipt, fused into one object so a caller can never
    hold a decision without also holding the truth of whether money moved.

    ``executed`` is True only when the decision was APPROVE *and* the payment
    actually reached the Stripe rail. ``stripe_id`` is the real settlement
    handle (``obp_test_...`` outbound payment, ``cs_...`` checkout) when the live
    rail produced one; it stays None on the recording stub and on every
    block/escalate — so a None stripe_id is proof no money moved on this call.
    """

    decision: DecisionKind
    reason: str
    policy_refs: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    decided_by: DecisionLayer = DecisionLayer.RULES
    executed: bool = False
    stripe_id: Optional[str] = None
    stripe_backend: str = "stub"
    event_id: str = ""

    @property
    def moved_money(self) -> bool:
        """True iff this settlement actually moved money on the rail."""
        return self.executed and self.decision == DecisionKind.APPROVE

    def as_policy_result(self) -> "PolicyResult":
        """The governance verdict alone, for callers typed on PolicyResult
        (e.g. the operator's refusal hook). Drops the rail receipt."""
        return PolicyResult(
            decision=self.decision,
            reason=self.reason,
            policy_refs=list(self.policy_refs),
            risk_score=self.risk_score,
            decided_by=self.decided_by,
        )


@dataclass(frozen=True)
class SpendContext:
    """Per-job context for judging a delivery spend against its paid invoice.

    The business operator builds this the moment the agent decides whether to
    buy a tool to deliver a job it has already been paid for. It carries exactly
    what the reasoning layer needs to judge *on-goal + margin-safe*, and the same
    numbers the deterministic ``_self_spend_over_budget`` / ``_self_spend_off_goal``
    rules read — so the LLM's narrative and the rule that actually enforces the
    refusal can never disagree about the facts, only (revealingly) about the call.

    ``budget_remaining`` is the operator's margin-protected headroom for this job:
    ``revenue - protected_margin - already_spent``. Feeding it into the existing
    over-budget rule is what turns "don't spend past budget" into "don't spend
    past the point that kills the margin" with no change to the rules engine.
    """

    job_id: str
    job_title: str
    revenue: float
    protected_margin: float
    budget_remaining: float
    tool_name: str
    tool_category: str
    cost: float
    allowed_categories: tuple[str, ...]
    tool_rationale: str = ""
    currency: str = "GBP"

    @property
    def margin_if_bought(self) -> float:
        """The profit that would survive on this job if this spend is approved."""
        return self.revenue - self.cost

    def as_facts(self) -> dict:
        """Compact fact dict for the reasoning prompt + the dashboard card."""
        return {
            "job_id": self.job_id,
            "job_title": self.job_title,
            "revenue": self.revenue,
            "protected_margin": self.protected_margin,
            "margin_safe_budget_remaining": self.budget_remaining,
            "tool_name": self.tool_name,
            "tool_category": self.tool_category,
            "tool_cost": self.cost,
            "margin_if_bought": self.margin_if_bought,
            "allowed_categories": list(self.allowed_categories),
            "tool_rationale": self.tool_rationale or None,
            "currency": self.currency,
        }


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
