"""Load scenario fixtures from JSON and build AgentEvent objects."""

from __future__ import annotations

import json
from pathlib import Path

from .models import AgentEvent, EventKind

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def load_scenario(name: str) -> tuple[AgentEvent, str, dict]:
    """Load one scenario by file stem.

    Returns (event, expected_decision_kind, raw_json) — the raw dict is exposed
    so the runner can apply per-scenario context seeds (e.g. duplicate
    fingerprints that represent payments made before the demo started).
    """
    path = SCENARIOS_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    event = AgentEvent(
        kind=EventKind(data["kind"]),
        vendor_id=data.get("vendor_id"),
        invoice_id=data.get("invoice_id"),
        ref=data.get("ref"),
        amount=data.get("amount"),
        invoice_amount=data.get("invoice_amount"),
        currency=data.get("currency", "GBP"),
        vendor_known=data.get("vendor_known", False),
        vendor_history_count=data.get("vendor_history_count", 0),
        detail_change_evidence=data.get("detail_change_evidence", 0.0),
        message=data.get("message", ""),
        category=data.get("category"),
    )
    return event, data["expected_decision"], data


def list_scenarios() -> list[str]:
    """All scenario file stems, sorted (the demo playback order)."""
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json"))
