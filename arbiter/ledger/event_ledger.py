"""Append-only event ledger.

Every decision (approve / block / escalate) and every earn/spend event is
recorded here. The ledger is the single source of truth the dashboard reads
and the source the reinvest threshold watches.

No I/O — this is an in-memory ledger the demo runner and tests use. Alex's
dashboard reads ``EventLedger.entries``; a persistent backing store can be
slotted in later without changing the interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import AgentEvent, PolicyResult, DecisionKind


@dataclass
class LedgerEntry:
    """One row in the timeline."""

    timestamp: float
    event_id: str
    event_kind: str
    decision: str
    reason: str
    policy_refs: list[str]
    risk_score: float
    decided_by: str
    amount: Optional[float] = None
    currency: str = "GBP"
    category: Optional[str] = None
    demo_beat: str = ""


class EventLedger:
    """Append-only ledger. Entries are never mutated or deleted."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self._earnings: float = 0.0
        self._spend: float = 0.0

    def record(
        self,
        event: AgentEvent,
        result: PolicyResult,
        event_id: str,
        demo_beat: str = "",
    ) -> LedgerEntry:
        entry = LedgerEntry(
            timestamp=time.time(),
            event_id=event_id,
            event_kind=event.kind.value,
            decision=result.decision.value,
            reason=result.reason,
            policy_refs=list(result.policy_refs),
            risk_score=result.risk_score,
            decided_by=result.decided_by.value,
            amount=event.amount,
            currency=event.currency,
            category=event.category,
            demo_beat=demo_beat,
        )
        self.entries.append(entry)
        # bookkeeping for the earn / spend / reinvest loop
        if event.kind.value == "invoice_payment" and result.decision == DecisionKind.APPROVE:
            self._earnings += event.amount or 0.0
        if event.kind.value == "self_spend" and result.decision == DecisionKind.APPROVE:
            self._spend += event.amount or 0.0
        return entry

    @property
    def earnings(self) -> float:
        return self._earnings

    @property
    def spend(self) -> float:
        return self._spend

    @property
    def net(self) -> float:
        return self._earnings - self._spend

    def blocks(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.decision == "block"]

    def escalations(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.decision == "escalate"]

    def as_timeline(self) -> list[dict]:
        """Dashboard-friendly serialisation."""
        return [
            {
                "t": e.timestamp,
                "id": e.event_id,
                "kind": e.event_kind,
                "decision": e.decision,
                "reason": e.reason,
                "refs": e.policy_refs,
                "risk": e.risk_score,
                "layer": e.decided_by,
                "amount": e.amount,
                "currency": e.currency,
                "category": e.category,
                "beat": e.demo_beat,
            }
            for e in self.entries
        ]
