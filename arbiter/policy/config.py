"""Policy configuration: build a PolicyContext from owner-facing settings.

Keeps the owner's tunable governance (spend cap, allowed self-spend categories,
approved-supplier allowlist, rule thresholds) in one explicit place instead of
hardcoded literals scattered across cli / web / metrics. The demo and the
server build their context through here so the allowlist is a single source of
truth — change the approved suppliers in one spot and every entry point honours
it.

Deliberately dependency-free: a plain dict in, a validated PolicyContext out.
A YAML/JSON front-end can sit on top later, but the demo wires Python dicts
directly and does not need the extra dependency.
"""

from __future__ import annotations

from typing import Any, Optional

from ..models import PolicyContext


class PolicyConfigError(ValueError):
    """Raised when an owner policy dict is malformed. Message names the field."""


def _as_float(d: dict, key: str, default: float) -> float:
    if key not in d:
        return default
    try:
        return float(d[key])
    except (TypeError, ValueError):
        raise PolicyConfigError(f"'{key}' must be a number, got {d[key]!r}")


def _as_str_set(d: dict, key: str) -> Optional[set[str]]:
    """A list/set of vendor ids -> set[str]. Absent key -> None (control off)."""
    if key not in d or d[key] is None:
        return None
    val = d[key]
    if isinstance(val, (str, bytes)):
        raise PolicyConfigError(f"'{key}' must be a list of ids, not a bare string")
    try:
        items = list(val)
    except TypeError:
        raise PolicyConfigError(f"'{key}' must be a list of ids, got {val!r}")
    out: set[str] = set()
    for it in items:
        if not isinstance(it, str) or not it.strip():
            raise PolicyConfigError(f"'{key}' entries must be non-empty strings, got {it!r}")
        out.add(it.strip())
    return out


def policy_context_from_dict(cfg: dict[str, Any]) -> PolicyContext:
    """Build a validated PolicyContext from an owner-config dict.

    Recognised keys (all optional; sensible defaults preserve current behaviour):
      spend_cap: float                      — self-spend ceiling
      budget_remaining: float               — remaining self-spend budget
      allowed_categories: list[str]         — self-spend categories the agent may buy
      approved_payees: list[str] | null     — supplier allowlist; null = not enforced
      new_vendor_auto_threshold: float      — small-amount new-vendor escalate ceiling
      detail_change_evidence_threshold: float — strong-evidence bar for bank changes

    A malformed value raises PolicyConfigError naming the field — owner-facing,
    no stack trace, refuses to boot on bad config (fail closed on misconfig).
    """
    if not isinstance(cfg, dict):
        raise PolicyConfigError(f"policy config must be a mapping, got {type(cfg).__name__}")

    spend_cap = _as_float(cfg, "spend_cap", 100.0)
    if spend_cap <= 0:
        raise PolicyConfigError(f"'spend_cap' must be greater than zero, got {spend_cap}")

    approved = _as_str_set(cfg, "approved_payees")

    categories = cfg.get("allowed_categories")
    if categories is None:
        allowed_categories = {"fraud_detection", "ocr", "bank_reconciliation"}
    else:
        parsed = _as_str_set({"allowed_categories": categories}, "allowed_categories")
        allowed_categories = parsed if parsed is not None else set()

    ctx = PolicyContext(
        spend_cap=spend_cap,
        budget_remaining=_as_float(cfg, "budget_remaining", spend_cap),
        allowed_categories=allowed_categories,
        approved_payees=approved,
        new_vendor_auto_threshold=_as_float(cfg, "new_vendor_auto_threshold", 50.0),
        detail_change_evidence_threshold=_as_float(cfg, "detail_change_evidence_threshold", 0.8),
    )
    return ctx


# The demo's owner profile: a small service business that has approved exactly
# three recurring suppliers. This is the allowlist the business-day scenario set
# is governed by — referenced by the runner so the demo and the tests agree.
DEMO_OWNER_POLICY: dict[str, Any] = {
    "spend_cap": 1000.0,
    "budget_remaining": 1000.0,
    "allowed_categories": ["fraud_detection", "ocr", "bank_reconciliation"],
    "approved_payees": ["aws", "northstar_studio", "acme_print"],
}


def demo_policy_context() -> PolicyContext:
    """PolicyContext for the business-day demo — the 3-supplier allowlist."""
    return policy_context_from_dict(DEMO_OWNER_POLICY)
