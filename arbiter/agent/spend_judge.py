"""Spend-judgement reasoning layer for the business-operator loop.

The operator decides whether to buy a tool to deliver a paid job. The *hard*
gate on that spend stays the deterministic rules engine (``_self_spend_off_goal``
+ ``_self_spend_over_budget``) running inside ``ArbiterAgent.decide`` — a raw
model never moves money here either. This layer sits *beside* that gate and
produces a visible reasoning narrative: "is this purchase on-goal for the job,
and does it keep the protected margin?"

Why both, and why this is the strongest governance story we can tell:

* The rule is the law. It is pure, testable, and cannot be talked out of a
  refusal.
* The reasoning layer is the *explanation* — and the demo's sharpest beat is the
  case where the model would happily approve a spend ("looks useful, buy it")
  yet the margin rule refuses it anyway. Reasoning advises; policy decides.

So this layer returns an ADVISORY ``SpendJudgement`` (approve/refuse/escalate +
reason), never a money movement. The operator records it next to the
rules-engine decision so the dashboard can show both — model said X, policy did
Y — which is exactly the "you can actually trust it with money" point.

Offline it uses ``MockSpendJudge`` (deterministic, no network). With a live
``NVIDIA_API_KEY`` it uses ``NimSpendJudge``, a real Nemotron call on the spend
path. Either way, malformed / unreachable output degrades to ESCALATE (advise a
human), never to APPROVE — the same fail-safe the rest of the engine uses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Protocol

from ..models import DecisionKind, SpendContext


def _message_text(message) -> str:
    """Pull the JSON answer text from a chat-completion message.

    Reasoning Nemotron variants put their chain-of-thought in a separate
    ``reasoning_content`` field and the strict-JSON answer in ``content``. We
    want ``content``; but if a model with thinking enabled ever returns an empty
    ``content`` (whole budget spent reasoning) while leaving the JSON in
    ``reasoning_content``, fall back to that so the hardened parser still gets a
    shot at the object instead of seeing an empty string and degrading to the
    malformed fail-safe. Returns "" only when truly nothing came back.
    """
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content
    return (getattr(message, "reasoning_content", None) or "").strip()


@dataclass(frozen=True)
class SpendJudgement:
    """Advisory verdict on a delivery spend. Never moves money itself."""

    decision: DecisionKind
    reason: str
    margin_ok: bool
    on_goal: bool
    risk_score: float
    source: str  # "mock" | "nim:<model>" | "nim_unreachable" | "malformed"
    raw: str = ""

    def as_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "margin_ok": self.margin_ok,
            "on_goal": self.on_goal,
            "risk_score": round(self.risk_score, 4),
            "source": self.source,
        }


class SpendJudge(Protocol):
    """Anything that can advise on a delivery spend."""

    def judge_spend(self, spend: SpendContext) -> SpendJudgement: ...


def _facts_truth(spend: SpendContext) -> tuple[bool, bool]:
    """Ground-truth (on_goal, margin_ok) the operator already knows from numbers.

    The reasoning layer is asked to *reach* these conclusions from the facts; we
    compute them independently so a judgement that contradicts arithmetic can be
    caught and safely downgraded. ``margin_ok`` mirrors the exact condition the
    deterministic over-budget rule enforces (cost <= margin-safe headroom).
    """
    on_goal = spend.tool_category in spend.allowed_categories
    margin_ok = spend.cost <= spend.budget_remaining
    return on_goal, margin_ok


_SYSTEM_PROMPT = (
    "You are the spend-judgement reasoning layer of Arbiter, an autonomous "
    "money-operator running the back office of a service business. The business "
    "has already been PAID for a job. You are deciding whether buying one tool "
    "to deliver that job is a good business decision.\n\n"
    "Judge two things and nothing else:\n"
    "1. on_goal: is the tool's category in the allowed set for delivering work?\n"
    "2. margin_ok: does the tool's cost stay within the margin-safe budget "
    "remaining for THIS job? Spending more than that would eat the protected "
    "profit margin — never acceptable, however useful the tool seems.\n\n"
    "Respond with STRICT JSON and nothing else, exactly:\n"
    '{\"decision\": \"approve|block|escalate\", \"on_goal\": true|false, '
    '\"margin_ok\": true|false, \"risk_score\": 0.0-1.0, \"reason\": \"<one sentence>\"}\n\n'
    "Approve ONLY when both on_goal and margin_ok are true. If the spend would "
    "break the margin, you must NOT approve it even if the tool is useful — "
    "block it and say it would make the job unprofitable. If the category is "
    "off-goal, block it. When genuinely unsure, escalate to the human owner."
)


def _build_spend_prompt(spend: SpendContext) -> str:
    return (
        "Decide whether to buy this tool to deliver a job you've already been "
        "paid for. Return only the strict JSON object from the system prompt.\n\n"
        f"{json.dumps(spend.as_facts(), indent=2)}"
    )


def _coerce_spend(raw: str, spend: SpendContext, source: str) -> SpendJudgement:
    """Parse a strict-JSON spend verdict; clamp it to arithmetic ground truth.

    Two layers of safety:
      1. Unparseable / missing decision -> ESCALATE (advise a human), never approve.
      2. A parsed APPROVE that contradicts the real numbers (off-goal, or cost
         over the margin-safe budget) is downgraded to BLOCK. The reasoning layer
         can *explain* a refusal but is never allowed to manufacture an approval
         the facts don't support — the deterministic rule would refuse it anyway,
         and the dashboard must not show the model "approving" margin-breaking spend.
    """
    from .nemotron import _parse_strict_json  # reuse the hardened parser

    on_goal_truth, margin_ok_truth = _facts_truth(spend)
    parsed = _parse_strict_json(raw)
    if parsed is None:
        return SpendJudgement(
            decision=DecisionKind.ESCALATE,
            reason=f"Spend-judge returned malformed JSON — escalating to owner. raw={raw[:80]!r}",
            margin_ok=margin_ok_truth,
            on_goal=on_goal_truth,
            risk_score=0.6,
            source="malformed",
            raw=raw,
        )
    try:
        decision = DecisionKind(parsed["decision"])
    except (KeyError, ValueError):
        return SpendJudgement(
            decision=DecisionKind.ESCALATE,
            reason=f"Spend-judge JSON missing/invalid 'decision' — escalating. raw={raw[:80]!r}",
            margin_ok=margin_ok_truth,
            on_goal=on_goal_truth,
            risk_score=0.6,
            source="malformed",
            raw=raw,
        )

    on_goal = bool(parsed.get("on_goal", on_goal_truth))
    margin_ok = bool(parsed.get("margin_ok", margin_ok_truth))
    risk = float(parsed.get("risk_score", 0.5))
    reason = str(parsed.get("reason", ""))[:240]

    # Clamp: an approval that breaks the real numbers is not allowed to stand.
    if decision == DecisionKind.APPROVE and not (on_goal_truth and margin_ok_truth):
        broke = "would eat the protected margin" if not margin_ok_truth else "is off-goal for delivery"
        return SpendJudgement(
            decision=DecisionKind.BLOCK,
            reason=(
                f"Reasoning layer leaned approve, but the spend {broke} "
                f"(cost {spend.cost} vs margin-safe budget {spend.budget_remaining}) — refused."
            ),
            margin_ok=margin_ok_truth,
            on_goal=on_goal_truth,
            risk_score=max(risk, 0.7),
            source=source,
            raw=raw,
        )
    return SpendJudgement(
        decision=decision,
        reason=reason or "Spend judged against job margin and goal.",
        margin_ok=margin_ok,
        on_goal=on_goal,
        risk_score=risk,
        source=source,
        raw=raw,
    )


class MockSpendJudge:
    """Deterministic, no-network spend judge for the offline demo and tests.

    Reaches the same verdict the facts imply, with a human-readable reason, so
    the offline operator loop tells the identical story the live NIM path does.
    Crucially it APPROVES a useful, in-budget tool and BLOCKS one that would
    break the margin — including the case where the tool is genuinely useful but
    simply costs too much for this job. That margin block is the signature beat.
    """

    def judge_spend(self, spend: SpendContext) -> SpendJudgement:
        on_goal, margin_ok = _facts_truth(spend)
        if not on_goal:
            raw = json.dumps({
                "decision": "block", "on_goal": False, "margin_ok": margin_ok,
                "risk_score": 0.75,
                "reason": (
                    f"Tool category '{spend.tool_category}' is off-goal for delivery "
                    f"(allowed: {list(spend.allowed_categories)}) — not a delivery cost."
                ),
            })
        elif not margin_ok:
            raw = json.dumps({
                "decision": "block", "on_goal": True, "margin_ok": False,
                "risk_score": 0.8,
                "reason": (
                    f"Buying '{spend.tool_name}' at {spend.cost} would leave "
                    f"{spend.margin_if_bought} on a job that must protect a "
                    f"{spend.protected_margin} margin — it would make the job "
                    f"unprofitable, so the spend is refused."
                ),
            })
        else:
            raw = json.dumps({
                "decision": "approve", "on_goal": True, "margin_ok": True,
                "risk_score": 0.15,
                "reason": (
                    f"'{spend.tool_name}' ({spend.tool_category}, {spend.cost}) is on-goal "
                    f"and keeps {spend.margin_if_bought} profit above the "
                    f"{spend.protected_margin} margin — approved to deliver the job."
                ),
            })
        return _coerce_spend(raw, spend, source="mock")


# --- live NVIDIA NIM spend judge --------------------------------------------

DEFAULT_SPEND_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _resolve_api_key() -> Optional[str]:
    """Read the NVIDIA key from the canonical env var or its accepted alias.

    Primary: ``NVIDIA_API_KEY`` (the engine's existing convention). Alias:
    ``NVIDIA_NIM_KEY`` so a drop using either name works without a code change.
    """
    return os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_KEY")


class NimSpendJudge:
    """Real Nemotron spend-judgement via NVIDIA NIM. Advisory only — moves no money."""

    def __init__(self, api_key: str, model: str = DEFAULT_SPEND_MODEL,
                 base_url: str = NIM_BASE_URL, timeout: float = 30.0) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> Optional["NimSpendJudge"]:
        key = _resolve_api_key()
        if not key:
            return None
        return cls(api_key=key, model=model or os.environ.get("NVIDIA_NIM_MODEL", DEFAULT_SPEND_MODEL))

    def judge_spend(self, spend: SpendContext) -> SpendJudgement:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_spend_prompt(spend)},
                ],
                temperature=0.2,
                # Reasoning Nemotron variants spend 150-310 completion tokens
                # *thinking* before they emit the JSON answer; a 512 cap let a
                # long reasoning trace truncate the answer on the margin-killer
                # beat (the demo's climax), dropping it to the malformed
                # fail-safe. 1024 gives the JSON ~3x headroom over the worst
                # observed trace so the real narrative always lands.
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            raw = _message_text(resp.choices[0].message)
            return _coerce_spend(raw, spend, source=f"nim:{self.model}")
        except Exception as exc:  # network / auth / rate-limit: advise a human, never approve
            raw = json.dumps({
                "decision": "escalate",
                "on_goal": _facts_truth(spend)[0],
                "margin_ok": _facts_truth(spend)[1],
                "risk_score": 0.6,
                "reason": f"NIM spend-judge call failed ({type(exc).__name__}) — escalating to owner.",
            })
            j = _coerce_spend(raw, spend, source="nim_unreachable")
            return j


def select_spend_judge(model: Optional[str] = None) -> SpendJudge:
    """Real NIM spend judge when a key is present, else the deterministic mock.

    Prints which layer is active so the demo can show at boot that the spend
    decision is judged by a real Nemotron model rather than a stub.
    """
    nim = NimSpendJudge.from_env(model=model)
    if nim is not None:
        print(f"[arbiter] Spend-judge layer: REAL NVIDIA NIM ({nim.model})")
        return nim
    print("[arbiter] Spend-judge layer: MockSpendJudge (no NVIDIA_API_KEY set)")
    return MockSpendJudge()
