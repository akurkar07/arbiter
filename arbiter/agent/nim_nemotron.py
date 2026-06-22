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
    NVIDIA_NIM_MODEL optional; defaults to nvidia/llama-3.1-nemotron-ultra-253b-v1

``from_env`` returns ``None`` when no key is present, so the agent can fall
back to ``MockNemotron`` and the demo never hard-fails offline.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..models import AgentEvent, PolicyResult
from .nemotron import NemotronResult, _coerce

DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
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
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> Optional["NimNemotron"]:
        """Build from NVIDIA_API_KEY, or None if the key is absent."""
        key = os.environ.get("NVIDIA_API_KEY")
        if not key:
            return None
        return cls(api_key=key, model=model or os.environ.get("NVIDIA_NIM_MODEL", DEFAULT_MODEL))

    def judge(self, event: AgentEvent, policy_hint: PolicyResult) -> NemotronResult:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(event, policy_hint)},
                ],
                temperature=0.2,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
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
        print(f"[arbiter] Nemotron layer: REAL NVIDIA NIM ({nim.model})")
        return nim
    print("[arbiter] Nemotron layer: MockNemotron (no NVIDIA_API_KEY set)")
    return MockNemotron()
