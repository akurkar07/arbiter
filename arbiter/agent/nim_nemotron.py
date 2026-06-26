"""Real Nemotron reasoning layer via NVIDIA NIM.

Drop-in for ``MockNemotron``: same ``judge(event, policy_hint) -> NemotronResult``
shape (the ``NemotronLayer`` protocol). The only difference is that the strict
JSON comes back from a real Nemotron model on NVIDIA NIM instead of a canned
dict.

The layer stays bounded the same way the mock is: it may only *refine* a
decision the rules layer already escalated. It never holds a Stripe tool and
never moves money. Malformed or unreachable -> the existing ``_coerce`` keeps
the rules-layer escalate, so a flaky network can never turn into an approval.

NIM is OpenAI-compatible, so we drive it with the ``openai`` SDK pointed at
``https://integrate.api.nvidia.com/v1``. Configure with two env vars:

    NVIDIA_API_KEY   required; the nvapi-... key from build.nvidia.com
                     (NVIDIA_NIM_KEY is accepted as an alias)
    NVIDIA_NIM_MODEL optional; defaults to nvidia/llama-3.3-nemotron-super-49b-v1

``from_env`` returns ``None`` when no key is present, so the agent can fall
back to ``MockNemotron`` and the demo never hard-fails offline.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..models import AgentEvent, PolicyResult
from .nemotron import NemotronResult, _coerce
from .spend_judge import _message_text

DEFAULT_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

_SYSTEM_PROMPT = (
    "You are the bounded reasoning layer of Arbiter, a self-governing AI "
    "payment-ops agent. A deterministic rules engine has ALREADY decided to "
    "escalate one money decision because it is ambiguous. Your only job is to "
    "refine that single decision. You never move money and you hold no tools.\n\n"
    "Respond with a STRICT JSON object and nothing else, exactly:\n"
    '{"decision": "approve|block|escalate", "risk_score": 0.0-1.0, '
    '"reason": "<one sentence>", "policy_refs": ["<rule ids you relied on>"]}\n\n'
    "Rules for your judgement:\n"
    "- Approve ONLY when the evidence clearly supports it (e.g. a small amount "
    "from a new vendor with a plausible service match, or a known-vendor bank "
    "change backed by strong independent evidence).\n"
    "- Block when the event looks like fraud or policy violation.\n"
    "- When genuinely unsure, return escalate so a human owner decides. "
    "Escalate is always the safe answer."
)


def _build_user_prompt(event: AgentEvent, policy_hint: PolicyResult) -> str:
    """Serialize the escalated decision into the model's input."""
    facts = {
        "event_kind": event.kind.value,
        "amount": event.amount,
        "invoice_amount": event.invoice_amount,
        "currency": event.currency,
        "vendor_id": event.vendor_id,
        "vendor_known": event.vendor_known,
        "vendor_history_count": event.vendor_history_count,
        "detail_change_evidence": event.detail_change_evidence,
        "category": event.category,
        "message": event.message or None,
        "rules_layer_decision": policy_hint.decision.value,
        "rules_layer_reason": policy_hint.reason,
        "rules_layer_risk": policy_hint.risk_score,
        "rules_layer_policy_refs": policy_hint.policy_refs,
    }
    facts = {k: v for k, v in facts.items() if v is not None}
    return (
        "Refine this escalated payment decision. Return only the strict JSON "
        "object described in the system prompt.\n\n"
        f"{json.dumps(facts, indent=2)}"
    )


class NimNemotron:
    """Calls a real Nemotron model on NVIDIA NIM, bounded to refining escalations."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = NIM_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        # Imported lazily so the package still imports with no openai installed.
        from openai import OpenAI

        self.model = model
        self.base_url = base_url
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @property
    def provider(self) -> str:
        """Human-readable name of the endpoint actually in use, for honest
        boot banners: the real NVIDIA rail vs an OpenAI-compatible fallback."""
        host = self.base_url.lower()
        if "integrate.api.nvidia.com" in host:
            return "NVIDIA NIM"
        if "openrouter.ai" in host:
            return "OpenRouter"
        return self.base_url

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> Optional["NimNemotron"]:
        """Build from env, or None when no key is present.

        Two routes, picked by which key is set:

          * NVIDIA NIM (default): NVIDIA_API_KEY (or NVIDIA_NIM_KEY alias) +
            the integrate.api.nvidia.com base url.
          * OpenRouter fallback: if NVIDIA_NIM_BASE_URL is set (e.g. when the
            NVIDIA key has no inference entitlement and 403s), the same client
            talks to that OpenAI-compatible endpoint instead. OpenRouter hosts
            free Nemotron variants and supports response_format json_object, so
            the bounded-judgement contract is unchanged — only the URL moves.

        Resolution order for the key: NVIDIA_API_KEY, NVIDIA_NIM_KEY, then
        OPENROUTER_API_KEY (so the fallback works with an or-key alone).
        """
        key = (
            os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("NVIDIA_NIM_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        if not key:
            return None
        base_url = os.environ.get("NVIDIA_NIM_BASE_URL", NIM_BASE_URL)
        return cls(
            api_key=key,
            model=model or os.environ.get("NVIDIA_NIM_MODEL", DEFAULT_MODEL),
            base_url=base_url,
        )

    def judge(self, event: AgentEvent, policy_hint: PolicyResult) -> NemotronResult:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(event, policy_hint)},
                ],
                temperature=0.2,
                # See spend_judge._message_text: this reasoning model spends
                # 150-310 tokens thinking before the JSON answer; 1024 keeps the
                # bounded-decision JSON from being truncated into the malformed
                # fail-safe when a reasoning trace runs long.
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            raw = _message_text(resp.choices[0].message)
        except Exception as exc:  # network/auth/rate-limit: stay safe, keep escalate
            raw = json.dumps(
                {
                    "decision": "escalate",
                    "risk_score": policy_hint.risk_score,
                    "reason": f"NIM call failed ({type(exc).__name__}) — upholding rules-layer escalate.",
                    "policy_refs": policy_hint.policy_refs + ["nim_unreachable"],
                }
            )
        # Same strict parser/fail-safe the mock uses: malformed -> escalate.
        return _coerce(raw, policy_hint)


def select_nemotron(model: Optional[str] = None):
    """Return the real NIM layer when NVIDIA_API_KEY is set, else the mock.

    Centralises the mock-vs-real choice so every construction site (web server,
    CLI) behaves identically. Prints which layer is active so the demo can show,
    at boot, that it is talking to a real Nemotron model rather than the stub.
    """
    from .nemotron import MockNemotron

    nim = NimNemotron.from_env(model=model)
    if nim is not None:
        print(f"[arbiter] Nemotron layer: REAL {nim.provider} ({nim.model})")
        return nim
    print("[arbiter] Nemotron layer: MockNemotron (no NVIDIA_API_KEY set)")
    return MockNemotron()


def selftest(model: Optional[str] = None) -> int:
    """Prove the live NIM path end-to-end: one real call on the canonical case.

    Runs the bounded reasoning layer against the ambiguous known-vendor
    bank-detail change — the exact escalation it exists to refine — and prints
    the raw model response plus the parsed, validated decision. Returns a
    process exit code so CI / a demo check can assert the integration is live:

        0  real NIM call succeeded and returned a valid bounded decision
        1  no NVIDIA_API_KEY (cannot test the live path)
        2  the call ran but the model/credentials were rejected or unusable

    This is the receipt that distinguishes "the layer is wired" from "the layer
    actually talks to Nemotron." It never moves money — it only judges.
    """
    from ..models import AgentEvent, EventKind, PolicyResult, DecisionKind, DecisionLayer

    nim = NimNemotron.from_env(model=model)
    if nim is None:
        print("[selftest] No inference key set — cannot exercise the live path.")
        print("[selftest] Set NVIDIA_API_KEY (nvapi-... from build.nvidia.com), or")
        print("[selftest] OPENROUTER_API_KEY + NVIDIA_NIM_BASE_URL=https://openrouter.ai/api/v1")
        print("[selftest] with NVIDIA_NIM_MODEL=<a free nemotron id>, and retry.")
        return 1

    print(f"[selftest] Calling real {nim.provider} model: {nim.model}")
    event = AgentEvent(
        kind=EventKind.VENDOR_DETAIL_CHANGE,
        vendor_id="vendor_stark",
        vendor_known=True,
        vendor_history_count=8,
        detail_change_evidence=0.25,
        message="Hi, please update our bank details to sort 11-22-33 account 98765432. Thanks.",
    )
    hint = PolicyResult(
        decision=DecisionKind.ESCALATE,
        reason="Known vendor requesting bank-detail change with weak evidence. Owner must confirm.",
        policy_refs=["vendor_detail_change_known_vendor"],
        risk_score=0.6,
        decided_by=DecisionLayer.RULES,
    )
    result = nim.judge(event, hint)
    print(f"[selftest] raw model output:\n{result.raw}\n")
    print(f"[selftest] parsed decision : {result.decision.value}")
    print(f"[selftest] risk_score      : {result.risk_score}")
    print(f"[selftest] reason          : {result.reason}")
    print(f"[selftest] policy_refs     : {result.policy_refs}")

    # If the call fell back to the unreachable/ malformed safe-default, the live
    # path did not actually succeed — surface that as a non-zero code.
    refs = set(result.policy_refs)
    if {"nim_unreachable", "llm_malformed", "llm_invalid_decision"} & refs:
        print("[selftest] FAIL: call did not return a usable bounded decision "
              "(credentials/model rejected or unparseable output).")
        return 2
    print("[selftest] OK: real Nemotron returned a valid bounded decision.")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    # Lets `python -m arbiter.agent.nim_nemotron` prove the live path and
    # propagate the selftest exit code (0 ok / 1 no key / 2 rejected) so a
    # deploy check or Atlas's key-drop verification can assert on it.
    import sys

    sys.exit(selftest())

