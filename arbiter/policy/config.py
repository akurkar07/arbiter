"""Policy configuration: build a PolicyContext from owner-facing settings.

Keeps the owner's tunable governance (spend cap, allowed self-spend categories,
approved-supplier allowlist, rule thresholds) in one explicit place instead of
hardcoded literals scattered across cli / web / metrics. The demo and the
server build their context through here so the allowlist is a single source of
truth.

Deliberately dependency-free: a plain dict in, a validated PolicyContext out.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

from ..models import PolicyContext


class PolicyConfigError(ValueError):
    """Raised when an owner policy dict is malformed. Message names the field."""


POLICY_KEYS = {
    "spend_cap",
    "budget_remaining",
    "allowed_categories",
    "approved_payees",
    "duplicate_lookback",
    "new_vendor_auto_threshold",
    "detail_change_evidence_threshold",
}


def _as_float(d: dict, key: str, default: float) -> float:
    if key not in d:
        return default
    if isinstance(d[key], bool):
        raise PolicyConfigError(f"'{key}' must be a number, got {d[key]!r}")
    try:
        return float(d[key])
    except (TypeError, ValueError):
        raise PolicyConfigError(f"'{key}' must be a number, got {d[key]!r}")


def _as_int(d: dict, key: str, default: int) -> int:
    if key not in d:
        return default
    if isinstance(d[key], bool):
        raise PolicyConfigError(f"'{key}' must be an integer, got {d[key]!r}")
    value = d[key]
    if isinstance(value, str):
        if not value.strip().isdigit():
            raise PolicyConfigError(f"'{key}' must be an integer, got {value!r}")
        return int(value)
    if not isinstance(value, int):
        raise PolicyConfigError(f"'{key}' must be an integer, got {value!r}")
    return value


def _as_str_set(d: dict, key: str) -> Optional[set[str]]:
    """A list/set of ids -> set[str]. Absent key -> None (control off)."""
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

    Recognised keys, all optional:
      spend_cap: float
      budget_remaining: float
      allowed_categories: list[str]
      approved_payees: list[str] | null
      duplicate_lookback: int
      new_vendor_auto_threshold: float
      detail_change_evidence_threshold: float in [0.0, 1.0]
    """
    if not isinstance(cfg, dict):
        raise PolicyConfigError(f"policy config must be a mapping, got {type(cfg).__name__}")
    unknown = sorted(set(cfg) - POLICY_KEYS)
    if unknown:
        raise PolicyConfigError(f"unknown policy key(s): {', '.join(unknown)}")

    spend_cap = _as_float(cfg, "spend_cap", 100.0)
    if spend_cap <= 0:
        raise PolicyConfigError(f"'spend_cap' must be greater than zero, got {spend_cap}")

    budget_remaining = _as_float(cfg, "budget_remaining", spend_cap)
    if budget_remaining < 0:
        raise PolicyConfigError(f"'budget_remaining' must be zero or greater, got {budget_remaining}")

    approved = _as_str_set(cfg, "approved_payees")

    categories = cfg.get("allowed_categories")
    if categories is None:
        allowed_categories = {"fraud_detection", "ocr", "bank_reconciliation"}
    else:
        parsed = _as_str_set({"allowed_categories": categories}, "allowed_categories")
        allowed_categories = parsed if parsed is not None else set()

    duplicate_lookback = _as_int(cfg, "duplicate_lookback", 50)
    if duplicate_lookback < 1:
        raise PolicyConfigError(f"'duplicate_lookback' must be >= 1, got {duplicate_lookback}")

    new_vendor_auto_threshold = _as_float(cfg, "new_vendor_auto_threshold", 50.0)
    if new_vendor_auto_threshold < 0:
        raise PolicyConfigError(
            f"'new_vendor_auto_threshold' must be zero or greater, got {new_vendor_auto_threshold}"
        )

    detail_change_evidence_threshold = _as_float(cfg, "detail_change_evidence_threshold", 0.8)
    if not 0.0 <= detail_change_evidence_threshold <= 1.0:
        raise PolicyConfigError(
            "'detail_change_evidence_threshold' must be between 0.0 and 1.0, "
            f"got {detail_change_evidence_threshold}"
        )

    return PolicyContext(
        spend_cap=spend_cap,
        budget_remaining=budget_remaining,
        allowed_categories=allowed_categories,
        approved_payees=approved,
        duplicate_lookback=duplicate_lookback,
        new_vendor_auto_threshold=new_vendor_auto_threshold,
        detail_change_evidence_threshold=detail_change_evidence_threshold,
    )


def policy_config_from_context(ctx: PolicyContext) -> dict[str, Any]:
    """Serialize PolicyContext into the owner-facing shape the dashboard edits."""
    return {
        "spend_cap": ctx.spend_cap,
        "budget_remaining": ctx.budget_remaining,
        "allowed_categories": sorted(ctx.allowed_categories),
        "approved_payees": sorted(ctx.approved_payees) if ctx.approved_payees is not None else None,
        "duplicate_lookback": ctx.duplicate_lookback,
        "new_vendor_auto_threshold": ctx.new_vendor_auto_threshold,
        "detail_change_evidence_threshold": ctx.detail_change_evidence_threshold,
    }


def normalize_policy_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a canonical owner policy dict."""
    return policy_config_from_context(policy_context_from_dict(cfg))


# The demo's owner profile: a small service business that has approved exactly
# three recurring suppliers. This is the allowlist the business-day scenario set
# is governed by, referenced by the runner so the demo and the tests agree.
DEMO_OWNER_POLICY: dict[str, Any] = {
    "spend_cap": 1000.0,
    "budget_remaining": 1000.0,
    "allowed_categories": ["fraud_detection", "ocr", "bank_reconciliation"],
    "approved_payees": ["aws", "northstar_studio", "acme_print"],
    "duplicate_lookback": 50,
    "new_vendor_auto_threshold": 50.0,
    "detail_change_evidence_threshold": 0.8,
}


def demo_owner_policy() -> dict[str, Any]:
    """Fresh mutable copy of the demo owner policy."""
    return deepcopy(DEMO_OWNER_POLICY)


def demo_policy_context() -> PolicyContext:
    """PolicyContext for the business-day demo."""
    return policy_context_from_dict(DEMO_OWNER_POLICY)
