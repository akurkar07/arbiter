"""Bounded Nemotron reasoning layer.

Strict JSON in, strict JSON out. The LLM never holds a Stripe tool and never
decides money movement on its own — it only *refines* a decision the rules
layer deferred (escalate). If it returns malformed output, the decision stays
``escalate``. This is the NVIDIA sponsor-tech integration point.

The real implementation will call Nemotron 3 Ultra via NIM. For local dev and
tests we ship ``MockNemotron``, which returns deterministic, scenario-aware
JSON so the full agent loop runs with zero network and zero API key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Protocol

from ..models import AgentEvent, DecisionKind, PolicyResult, DecisionLayer


@dataclass(frozen=True)
class NemotronResult:
    decision: DecisionKind
    risk_score: float
    reason: str
    policy_refs: list[str]
    raw: str


class NemotronLayer(Protocol):
    """Any callable matching this shape can be the LLM layer."""

    def judge(self, event: AgentEvent, policy_hint: PolicyResult) -> NemotronResult: ...


def _strip_reasoning(raw: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks a reasoning model may emit.

    Nemotron reasoning variants (and several NIM models with thinking enabled)
    prepend a ``<think>`` block before the answer. That block can itself contain
    brace characters, which would otherwise capture the naive ``{...}`` span and
    corrupt the parse. Strip complete think blocks, and as a fallback drop a
    dangling unterminated one, before we look for the JSON object.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)
    # An unterminated <think> (model hit the token cap mid-reasoning): drop from
    # the tag to the first '{' that starts the actual JSON answer, if any.
    if "<think>" in cleaned.lower():
        lowered = cleaned.lower()
        tag = lowered.index("<think>")
        brace = cleaned.find("{", tag)
        cleaned = cleaned[brace:] if brace != -1 else cleaned[:tag]
    return cleaned


def _parse_strict_json(raw: str) -> Optional[dict]:
    """Extract the first JSON object from raw text. None if malformed.

    Robust to the two shapes a real Nemotron returns around the strict object:
    a ``<think>`` reasoning preamble and ```` ```json ```` code fences. We strip
    reasoning first, then scan for the first brace-balanced object rather than a
    greedy ``{.*}`` span, so braces inside any surviving prose can't swallow the
    real payload. Anything that still doesn't parse returns None, which the
    caller turns into a safe re-escalate — a malformed model response can never
    become an approval.
    """
    text = _strip_reasoning(raw)
    # Fast path: a clean or fenced object parses directly once fences are gone.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # General path: find the first brace-balanced {...} and parse it.
    candidate = _first_balanced_object(text)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _first_balanced_object(text: str) -> Optional[str]:
    """Return the first brace-balanced ``{...}`` substring, or None.

    Tracks string literals and escapes so a ``}`` inside a JSON string value
    doesn't end the object early. This is what lets a reason string containing a
    brace or quote survive the extraction.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _coerce(raw: str, fallback: PolicyResult) -> NemotronResult:
    """Parse LLM output; on any failure, keep the rules-layer escalate."""
    parsed = _parse_strict_json(raw)
    if parsed is None:
        return NemotronResult(
            decision=DecisionKind.ESCALATE,
            risk_score=fallback.risk_score,
            reason=f"LLM returned malformed JSON — falling back to rules-layer escalate. raw={raw[:80]!r}",
            policy_refs=fallback.policy_refs + ["llm_malformed"],
            raw=raw,
        )
    try:
        kind = DecisionKind(parsed["decision"])
    except (KeyError, ValueError):
        return NemotronResult(
            decision=DecisionKind.ESCALATE,
            risk_score=float(parsed.get("risk_score", fallback.risk_score)),
            reason=f"LLM JSON missing/invalid 'decision' — escalating. raw={raw[:80]!r}",
            policy_refs=fallback.policy_refs + ["llm_invalid_decision"],
            raw=raw,
        )
    return NemotronResult(
        decision=kind,
        risk_score=float(parsed.get("risk_score", fallback.risk_score)),
        reason=str(parsed.get("reason", ""))[:240],
        policy_refs=list(parsed.get("policy_refs", fallback.policy_refs)),
        raw=raw,
    )


class MockNemotron:
    """Deterministic, no-network stand-in for Nemotron 3 Ultra.

    Refines the few cases the rules layer escalates:
      - new_vendor_small_amount with a plausible service match → approve
      - vendor_bank_change_known_vendor with strong evidence → approve
      - everything else → uphold the escalate (owner decides)
    """

    def judge(self, event: AgentEvent, policy_hint: PolicyResult) -> NemotronResult:
        # Produce a plausible strict-JSON string, then run it through the same
        # strict parser the real layer uses — so the mock exercises the real
        # validation path.
        if "new_vendor_small_amount" in policy_hint.policy_refs:
            raw = json.dumps({
                "decision": "approve",
                "risk_score": 0.2,
                "reason": "New vendor but small amount and service description matches an expected SaaS subscription.",
                "policy_refs": ["new_vendor_small_amount", "llm_refine"],
            })
        elif "vendor_detail_change_known_vendor" in policy_hint.policy_refs:
            if event.detail_change_evidence >= 0.8:
                raw = json.dumps({
                    "decision": "approve",
                    "risk_score": 0.15,
                    "reason": "Strong independent evidence supports the bank-detail change for this known vendor.",
                    "policy_refs": ["vendor_detail_change_known_vendor", "llm_refine"],
                })
            else:
                raw = json.dumps({
                    "decision": "escalate",
                    "risk_score": 0.6,
                    "reason": "Weak evidence for a known-vendor bank change — owner must confirm out-of-band.",
                    "policy_refs": ["vendor_detail_change_known_vendor", "llm_refine"],
                })
        else:
            raw = json.dumps({
                "decision": "escalate",
                "risk_score": policy_hint.risk_score,
                "reason": "LLM upholds rules-layer escalate: ambiguous money judgment, owner decides.",
                "policy_refs": policy_hint.policy_refs + ["llm_refine"],
            })
        return _coerce(raw, policy_hint)


# A module-level helper so the agent core can swap mock ↔ real NIM cleanly.
def to_policy_result(nr: NemotronResult) -> PolicyResult:
    return PolicyResult(
        decision=nr.decision,
        reason=nr.reason,
        policy_refs=nr.policy_refs,
        risk_score=nr.risk_score,
        decided_by=DecisionLayer.LLM,
    )
