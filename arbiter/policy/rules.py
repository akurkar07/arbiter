"""Deterministic policy engine.

This is the moat. A raw LLM never decides money movement: rules first, bounded
LLM second, phone escalation third. Each rule is a pure function that inspects
an ``AgentEvent`` + ``PolicyContext`` and returns a ``PolicyResult`` for a hard
pass/fail, or ``None`` to defer to the next rule. If no rule fires, the event
escalates — the safe default for anything the engine does not recognise.

Rules run in priority order: the most dangerous patterns (instruction override,
duplicate, amount mismatch) are checked before anything that might approve.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..models import (
    AgentEvent,
    DecisionKind,
    EventKind,
    PolicyContext,
    PolicyResult,
    DecisionLayer,
    INSTRUCTION_OVERRIDE_PHRASES,
)

RuleFn = Callable[[AgentEvent, PolicyContext], Optional[PolicyResult]]


# --- rule registry -----------------------------------------------------------
# Ordered. The first rule that returns a non-None PolicyResult wins.
_RULES: list[RuleFn] = []


def register_rule(fn: RuleFn) -> RuleFn:
    """Decorator: append a rule to the ordered registry."""
    _RULES.append(fn)
    return fn


# --- rules, most-dangerous-first ---------------------------------------------


@register_rule
def _instruction_override(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """A message instructing the agent to ignore its own policies -> BLOCK.

    Social-engineering / prompt-injection attempt. Checked first so no later
    rule can approve past it. Applies to every event kind that carries a message.
    """
    if not event.message:
        return None
    lowered = event.message.lower()
    hit = next((p for p in INSTRUCTION_OVERRIDE_PHRASES if p in lowered), None)
    if hit is None:
        return None
    return PolicyResult(
        decision=DecisionKind.BLOCK,
        reason=f"Instruction-override attempt detected: phrase '{hit}' asks the agent to bypass policy.",
        policy_refs=["instruction_override"],
        risk_score=0.95,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _payee_not_approved(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """A vendor payment to a payee not on the owner's allowlist -> BLOCK.

    The foundational control and the answer to "isn't it scary that it can pay
    anyone?" — it can't. The agent may only ever pay suppliers the owner
    explicitly approved. Checked before any approve path so even a clean,
    correct-amount, non-duplicate payment to an unapproved payee is stopped
    cold.

    Allowlist semantics on ``ctx.approved_payees``:
      * ``None`` -> not configured; control inert (every other rule still runs).
      * a ``set`` -> enforced strictly; ``vendor_id`` must be a member or BLOCK.
        An empty set therefore approves no payee at all (fail closed), and a
        payment with no identifiable ``vendor_id`` cannot be verified -> BLOCK.
    """
    if event.kind != EventKind.VENDOR_PAYMENT:
        return None
    if ctx.approved_payees is None:
        return None  # allowlist not configured -> control inert
    if event.vendor_id is not None and event.vendor_id in ctx.approved_payees:
        return None  # approved supplier -> defer to the normal rules below
    payee = event.vendor_id if event.vendor_id is not None else "<unidentified>"
    return PolicyResult(
        decision=DecisionKind.BLOCK,
        reason=(
            f"Payee '{payee}' is not on the owner's approved supplier allowlist "
            f"{sorted(ctx.approved_payees)}. The agent may only pay approved "
            "suppliers — payment held before any money moved."
        ),
        policy_refs=["payee_not_approved"],
        risk_score=0.8,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _duplicate_invoice(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Same vendor + amount + ref seen recently -> BLOCK (double payment)."""
    if event.kind not in (EventKind.INVOICE_PAYMENT, EventKind.VENDOR_PAYMENT):
        return None
    if event.vendor_id is None or event.amount is None or event.ref is None:
        return None
    fp = (event.vendor_id, event.amount, event.ref)
    if fp in ctx.recent_payment_fingerprints:
        return PolicyResult(
            decision=DecisionKind.BLOCK,
            reason=f"Duplicate payment fingerprint (vendor={event.vendor_id}, amount={event.amount}, ref={event.ref}) already seen.",
            policy_refs=["duplicate_invoice"],
            risk_score=0.9,
            decided_by=DecisionLayer.RULES,
        )
    return None


@register_rule
def _amount_mismatch(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Invoice amount != request amount -> BLOCK (over/under-payment)."""
    if event.kind not in (EventKind.INVOICE_PAYMENT, EventKind.VENDOR_PAYMENT):
        return None
    if event.invoice_amount is None or event.amount is None:
        return None
    if event.invoice_amount != event.amount:
        return PolicyResult(
            decision=DecisionKind.BLOCK,
            reason=(
                f"Amount mismatch: invoice={event.invoice_amount} {event.currency} "
                f"vs request={event.amount} {event.currency}."
            ),
            policy_refs=["amount_mismatch"],
            risk_score=0.85,
            decided_by=DecisionLayer.RULES,
        )
    return None


@register_rule
def _vendor_detail_change_new_vendor(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """A vendor we've never paid asking to change bank details -> BLOCK.

    Classic supplier-impersonation fraud: a fake vendor (or a hijacked new
    contact) tries to redirect payment before any payment history exists.
    """
    if event.kind != EventKind.VENDOR_DETAIL_CHANGE:
        return None
    if event.vendor_known or event.vendor_history_count > 0:
        return None
    return PolicyResult(
        decision=DecisionKind.BLOCK,
        reason="New vendor requesting payment-detail change with no payment history — likely supplier impersonation.",
        policy_refs=["vendor_detail_change_new_vendor"],
        risk_score=0.88,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _vendor_detail_change_known_vendor(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Known vendor changing details with weak evidence -> ESCALATE.

    A known vendor *can* legitimately change banks, but it's the highest-risk
    AR fraud pattern. Strong independent evidence could auto-approve; weak
    evidence asks the owner. The LLM layer can refine, but rules force the
    default to escalate.
    """
    if event.kind != EventKind.VENDOR_DETAIL_CHANGE:
        return None
    if not (event.vendor_known or event.vendor_history_count > 0):
        return None
    if event.detail_change_evidence >= 0.8:
        return None  # strong evidence -> let the LLM layer approve if it agrees
    return PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason=(
            f"Known vendor (history={event.vendor_history_count}) requesting bank-detail change "
            f"with weak evidence (score={event.detail_change_evidence:.2f}). Owner must confirm."
        ),
        policy_refs=["vendor_detail_change_known_vendor"],
        risk_score=0.6,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _self_spend_over_budget(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Agent spending above its remaining budget -> SELF-BLOCK."""
    if event.kind != EventKind.SELF_SPEND:
        return None
    if event.amount is None or event.amount <= ctx.budget_remaining:
        return None
    return PolicyResult(
        decision=DecisionKind.BLOCK,
        reason=(
            f"Self-spend {event.amount} exceeds remaining budget {ctx.budget_remaining}. "
            "The agent blocks its own over-budget purchase."
        ),
        policy_refs=["self_spend_over_budget"],
        risk_score=0.7,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _self_spend_off_goal(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Agent spending on a category outside the allowed set -> SELF-BLOCK.

    This is the demo's trust beat: the agent tries to buy a marketing tool,
    its own policy refuses because marketing is not a fraud/ops capability.
    """
    if event.kind != EventKind.SELF_SPEND:
        return None
    if event.category is None:
        return None
    if event.category in ctx.allowed_categories:
        return None
    return PolicyResult(
        decision=DecisionKind.BLOCK,
        reason=(
            f"Self-spend category '{event.category}' is not in the allowed set "
            f"{sorted(ctx.allowed_categories)}. Off-goal spend blocked by the agent's own policy."
        ),
        policy_refs=["self_spend_off_goal"],
        risk_score=0.75,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _self_spend_approved(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """Agent spending on an allowed category within budget -> APPROVE.

    The reinvest beat: earnings buy a fraud-detection / OCR / reconciliation
    capability, which measurably improves the next run.
    """
    if event.kind != EventKind.SELF_SPEND:
        return None
    if event.amount is None or event.category is None:
        return None
    if event.category not in ctx.allowed_categories:
        return None
    if event.amount > ctx.budget_remaining:
        return None  # handled by the over-budget rule already
    return PolicyResult(
        decision=DecisionKind.APPROVE,
        reason=(
            f"Self-spend on allowed capability '{event.category}' ({event.amount}) within budget "
            f"({ctx.budget_remaining} remaining). Autonomous reinvest approved."
        ),
        policy_refs=["self_spend_allowed"],
        risk_score=0.1,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _new_vendor_small_amount(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """New vendor, small amount, otherwise clean -> ESCALATE (LLM may refine).

    Rules can't tell a legitimate new supplier from a crafted one on amount
    alone, so the default is to escalate. The bounded LLM layer gets to weigh
    the message + service match; if it is unavailable or malformed, the
    escalate stands.
    """
    if event.kind != EventKind.VENDOR_PAYMENT:
        return None
    if event.vendor_known or event.vendor_history_count > 0:
        return None
    if event.amount is None or event.amount > 50.0:
        return None
    return PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason=(
            f"New vendor (history=0) with small amount {event.amount} — ambiguous. "
            "Escalating for bounded judgment; owner may be pinged if LLM is unsure."
        ),
        policy_refs=["new_vendor_small_amount"],
        risk_score=0.45,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _approved_supplier_payment(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """A clean payment to an established, allowlisted supplier -> APPROVE.

    The autopilot's positive path — the agent actually doing its job: paying a
    supplier the owner approved, that we have a payment relationship with, for
    the correct amount. This is what makes Arbiter a product (it pays) rather
    than just a tripwire (it blocks).

    Gated hard so autonomy == owner intent:
      * requires a configured allowlist with this payee on it — an unconfigured
        allowlist never auto-pays (falls through to the safe escalate);
      * requires an established relationship (known / has history) — the first
        payment to a brand-new approved supplier still escalates once;
      * requires the amount to reconcile against the stored invoice.
    The dangerous patterns (duplicate, amount mismatch, detail change) are
    registered earlier and fire first, so we only reach here on a clean event.
    """
    if event.kind != EventKind.VENDOR_PAYMENT:
        return None
    if ctx.approved_payees is None or event.vendor_id not in ctx.approved_payees:
        return None  # not an owner-approved payee -> never auto-pay here
    if not (event.vendor_known or event.vendor_history_count > 0):
        return None  # brand-new approved supplier escalates the first time
    if event.amount is None:
        return None
    if event.invoice_amount is not None and event.invoice_amount != event.amount:
        return None  # amount_mismatch already blocked this; guard anyway
    return PolicyResult(
        decision=DecisionKind.APPROVE,
        reason=(
            f"Approved supplier '{event.vendor_id}' paid {event.amount} {event.currency} "
            "— on the owner's allowlist, established relationship, amount reconciled."
        ),
        policy_refs=["approved_supplier_payment"],
        risk_score=0.1,
        decided_by=DecisionLayer.RULES,
    )


@register_rule
def _invoice_normal_paid(event: AgentEvent, ctx: PolicyContext) -> Optional[PolicyResult]:
    """A normal customer invoice payment with no red flags -> APPROVE / reconcile."""
    if event.kind != EventKind.INVOICE_PAYMENT:
        return None
    if event.invoice_amount is not None and event.amount is not None and event.invoice_amount != event.amount:
        return None  # amount_mismatch already handled this
    return PolicyResult(
        decision=DecisionKind.APPROVE,
        reason="Normal invoice payment reconciled — no policy red flags.",
        policy_refs=["invoice_normal_paid"],
        risk_score=0.05,
        decided_by=DecisionLayer.RULES,
    )


# --- engine entry point ------------------------------------------------------


def evaluate(event: AgentEvent, ctx: PolicyContext) -> PolicyResult:
    """Run all rules in priority order. First hit wins; else ESCALATE.

    Safe default: an event no rule recognises is never auto-approved — it
    escalates to the owner. This is the core governance guarantee.
    """
    for rule in _RULES:
        result = rule(event, ctx)
        if result is not None:
            return result
    return PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="No rule matched this event — defaulting to owner escalation (safe).",
        policy_refs=["no_rule_matched"],
        risk_score=0.5,
        decided_by=DecisionLayer.ESCALATE,
    )
