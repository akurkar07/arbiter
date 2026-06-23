"""Tests for the payee allowlist — the foundational 'can't pay anyone' control.

Two surfaces:
  * the rule behaviour in the engine (allowlist gates every approve path);
  * the config loader that builds the allowlist from an owner dict.

These lock the trust story: a clean, correct-amount, non-duplicate payment to a
supplier the owner did NOT approve is still blocked before any money moves; and
the autopilot's positive path only fires for an approved, established supplier.
"""

from __future__ import annotations

import pytest

from arbiter.models import AgentEvent, EventKind, DecisionKind, PolicyContext
from arbiter.policy.rules import evaluate
from arbiter.policy.config import (
    policy_context_from_dict,
    demo_policy_context,
    PolicyConfigError,
)


def _pay(vendor_id, amount=200.0, invoice_amount=200.0, **kw):
    return AgentEvent(
        kind=EventKind.VENDOR_PAYMENT,
        vendor_id=vendor_id,
        amount=amount,
        invoice_amount=invoice_amount,
        ref=kw.pop("ref", "INV-X"),
        **kw,
    )


# --- the allowlist as a hard gate -------------------------------------------

def test_off_list_payee_blocked_even_when_clean():
    """A correct-amount, non-duplicate payment to an unapproved payee -> BLOCK."""
    ctx = PolicyContext(approved_payees={"aws", "northstar_studio"})
    r = evaluate(_pay("sketchy_co", vendor_known=True, vendor_history_count=9), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert "payee_not_approved" in r.policy_refs


def test_on_list_established_supplier_approved():
    """An approved, established supplier, correct amount -> APPROVE (autopilot pays)."""
    ctx = PolicyContext(approved_payees={"aws"})
    r = evaluate(_pay("aws", vendor_known=True, vendor_history_count=5), ctx)
    assert r.decision is DecisionKind.APPROVE
    assert "approved_supplier_payment" in r.policy_refs


def test_on_list_but_brand_new_supplier_escalates_once():
    """Approved but never-paid supplier -> ESCALATE the first time (not auto-pay)."""
    ctx = PolicyContext(approved_payees={"northstar_studio"})
    r = evaluate(_pay("northstar_studio", amount=40.0, invoice_amount=40.0,
                      vendor_known=False, vendor_history_count=0), ctx)
    assert r.decision is DecisionKind.ESCALATE
    assert "payee_not_approved" not in r.policy_refs  # allowlist let it through


def test_on_list_wrong_amount_still_blocks_on_mismatch():
    """Allowlist passes an approved payee, but the amount-mismatch guard fires first."""
    ctx = PolicyContext(approved_payees={"acme_print"})
    r = evaluate(_pay("acme_print", amount=840.0, invoice_amount=480.0,
                      vendor_known=True, vendor_history_count=4), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert "amount_mismatch" in r.policy_refs


def test_unidentified_payee_blocked_when_allowlist_on():
    """No vendor_id but allowlist configured -> can't verify -> BLOCK (fail closed)."""
    ctx = PolicyContext(approved_payees={"aws"})
    r = evaluate(_pay(None, vendor_known=True, vendor_history_count=5), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert "payee_not_approved" in r.policy_refs


def test_empty_allowlist_fails_closed():
    """An empty allowlist approves nobody — even a known vendor is blocked."""
    ctx = PolicyContext(approved_payees=set())
    r = evaluate(_pay("aws", vendor_known=True, vendor_history_count=5), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert "payee_not_approved" in r.policy_refs


def test_unconfigured_allowlist_is_inert_backcompat():
    """approved_payees=None -> control off; legacy behaviour (no auto-pay) preserved."""
    ctx = PolicyContext(approved_payees=None)
    r = evaluate(_pay("aws", vendor_known=True, vendor_history_count=5), ctx)
    # Without an allowlist there is no positive pay rule, so the safe default stands.
    assert r.decision is DecisionKind.ESCALATE
    assert "payee_not_approved" not in r.policy_refs


def test_allowlist_does_not_touch_earn_side():
    """Customer invoice payments (earn) are unaffected by the supplier allowlist."""
    ctx = PolicyContext(approved_payees={"aws"})
    r = evaluate(
        AgentEvent(kind=EventKind.INVOICE_PAYMENT, vendor_id="cust_x",
                   amount=480.0, invoice_amount=480.0, ref="INV-1"),
        ctx,
    )
    assert r.decision is DecisionKind.APPROVE
    assert "invoice_normal_paid" in r.policy_refs


# --- the ordering guarantee: allowlist before approve -----------------------

def test_allowlist_checked_before_any_approve_path():
    """The off-list block must win even against an otherwise-approvable event.

    Proves registration order: _payee_not_approved is registered before
    _approved_supplier_payment, so an unapproved payee can never reach approve.
    """
    ctx = PolicyContext(approved_payees={"aws"})
    # 'meta_ads' is established + correct amount: would APPROVE if it were on the
    # list. It isn't -> must BLOCK on the allowlist, not approve.
    r = evaluate(_pay("meta_ads", vendor_known=True, vendor_history_count=12), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert r.policy_refs == ["payee_not_approved"]


# --- the config loader ------------------------------------------------------

def test_config_builds_allowlist_from_dict():
    ctx = policy_context_from_dict({"approved_payees": ["aws", "acme_print"], "spend_cap": 500})
    assert ctx.approved_payees == {"aws", "acme_print"}
    assert ctx.spend_cap == 500.0


def test_config_absent_allowlist_is_none():
    ctx = policy_context_from_dict({"spend_cap": 500})
    assert ctx.approved_payees is None


def test_config_rejects_bare_string_allowlist():
    with pytest.raises(PolicyConfigError, match="approved_payees"):
        policy_context_from_dict({"approved_payees": "aws"})


def test_config_rejects_empty_entry():
    with pytest.raises(PolicyConfigError, match="approved_payees"):
        policy_context_from_dict({"approved_payees": ["aws", "  "]})


def test_config_rejects_nonpositive_spend_cap():
    with pytest.raises(PolicyConfigError, match="spend_cap"):
        policy_context_from_dict({"spend_cap": -5})


def test_demo_policy_has_three_suppliers():
    ctx = demo_policy_context()
    assert ctx.approved_payees == {"aws", "northstar_studio", "acme_print"}


def test_config_drives_a_real_block():
    """End-to-end: a config-built context blocks an off-list payee through evaluate()."""
    ctx = demo_policy_context()
    r = evaluate(_pay("rogue_vendor", vendor_known=True, vendor_history_count=3), ctx)
    assert r.decision is DecisionKind.BLOCK
    assert "payee_not_approved" in r.policy_refs
