"""Tests for F3 — the procurement scout and its governed integration.

Two things must be true for F3 to be a moat and not a liability:

1. The scout actively optimises spend — it sources the cheapest catalog tool
   that clears a job's quality bar, and a model proposal can only ever select
   within the owner catalog (off-catalog / malicious proposals are canonicalised
   back to the safe baseline, never widening authority).

2. A sourced spend is still governed — the RED test: a scout proposal that would
   blow a job's protected margin is REFUSED by the existing over-budget rule,
   exactly like any hand-declared tool. Sourcing advises; policy decides.
"""

from __future__ import annotations

import pytest

from arbiter.agent import ArbiterAgent
from arbiter.agent.nemotron import MockNemotron
from arbiter.agent.escalation import EscalationHandler
from arbiter.ledger import EventLedger
from arbiter.models import DecisionKind, PolicyContext
from arbiter.operator import BusinessOperator, Job, ToolPurchase
from arbiter.procurement import (
    BELOW_QUALITY,
    NO_CANDIDATE,
    SELECTED,
    CatalogItem,
    CatalogSelector,
    ProcurementCatalog,
    ProcurementScout,
    SourcingRequest,
    demo_catalog,
)
from arbiter.stripe_glue import select_stripe


# --- catalog + scout unit tests --------------------------------------------


def test_catalog_candidates_are_cheapest_first_above_quality_bar() -> None:
    cat = demo_catalog()
    cands = cat.candidates("image_generation", min_quality=0.0)
    prices = [c.price for c in cands]
    assert prices == sorted(prices), "candidates must be cheapest-first"
    assert cands[0].item_id == "img_basic"


def test_quality_bar_excludes_substandard_tools() -> None:
    cat = demo_catalog()
    # Raise the bar above the £20 basic (0.78) so only mid/premium qualify.
    cands = cat.candidates("image_generation", min_quality=0.80)
    ids = {c.item_id for c in cands}
    assert "img_basic" not in ids
    assert {"img_mid", "img_premium"} <= ids


def test_scout_sources_cheapest_qualifying_tool() -> None:
    scout = ProcurementScout(demo_catalog())
    res = scout.source(
        SourcingRequest(capability="image_generation", job_id="j1", job_title="banners")
    )
    assert res.outcome == SELECTED
    assert res.chosen is not None
    assert res.chosen.item_id == "img_basic"
    assert res.chosen.price == 20.0
    # The "chose £20 over £45" story must be a real number, not a claim.
    assert res.savings_vs_premium == 25.0


def test_scout_refuses_when_no_tool_meets_quality_bar() -> None:
    scout = ProcurementScout(demo_catalog())
    res = scout.source(
        SourcingRequest(
            capability="image_generation", job_id="j1", job_title="banners", min_quality=0.99
        )
    )
    assert res.outcome == BELOW_QUALITY
    assert res.chosen is None


def test_scout_refuses_unknown_capability() -> None:
    scout = ProcurementScout(demo_catalog())
    res = scout.source(
        SourcingRequest(capability="time_travel", job_id="j1", job_title="impossible")
    )
    assert res.outcome == NO_CANDIDATE
    assert res.chosen is None


class _MaliciousSelector(CatalogSelector):
    """A model that tries to escape the catalog by returning a forged id."""

    def __init__(self, forged_id: str) -> None:
        self.forged_id = forged_id

    def select(self, candidates, request) -> str:
        return self.forged_id


def test_off_catalog_model_proposal_is_canonicalised_to_baseline() -> None:
    # The model returns an id that is not in the catalog at all. The scout must
    # refuse to invent it and fall back to the cheapest qualifying real item.
    scout = ProcurementScout(demo_catalog(), selector=_MaliciousSelector("img_FREE_99999"))
    res = scout.source(
        SourcingRequest(capability="image_generation", job_id="j1", job_title="banners")
    )
    assert res.outcome == SELECTED
    assert res.chosen is not None
    assert res.chosen.item_id == "img_basic"  # canonicalised back to safe baseline
    assert res.model_proposed_id == "img_FREE_99999"
    assert res.model_was_corrected is True


def test_model_cannot_cross_capabilities() -> None:
    # The model proposes a real id, but from the wrong capability (a compute tool
    # for an image job). Must be corrected back to an image tool.
    scout = ProcurementScout(demo_catalog(), selector=_MaliciousSelector("compute_premium"))
    res = scout.source(
        SourcingRequest(capability="image_generation", job_id="j1", job_title="banners")
    )
    assert res.chosen is not None
    assert res.chosen.capability == "image_generation"
    assert res.chosen.item_id == "img_basic"
    assert res.model_was_corrected is True


def test_model_may_pick_a_pricier_qualifying_item_within_catalog() -> None:
    # A legitimate in-catalog upgrade pick is honoured (the model has authority to
    # choose *within* the safe set, just not outside it).
    scout = ProcurementScout(demo_catalog(), selector=_MaliciousSelector("img_premium"))
    res = scout.source(
        SourcingRequest(capability="image_generation", job_id="j1", job_title="banners")
    )
    assert res.chosen is not None
    assert res.chosen.item_id == "img_premium"
    assert res.model_was_corrected is False


# --- governed integration: the RED test ------------------------------------


class _AutoApprove(EscalationHandler):
    def request_approval(self, event, result) -> DecisionKind:
        return DecisionKind.APPROVE


def _operator_with_scout(starting_balance: float = 100.0) -> BusinessOperator:
    ctx = PolicyContext(
        spend_cap=1000.0,
        budget_remaining=1000.0,
        allowed_categories={"design_assets", "compute", "api_credits", "data"},
    )
    agent = ArbiterAgent(
        ctx=ctx,
        ledger=EventLedger(),
        nemotron=MockNemotron(),
        escalation=_AutoApprove(),
        stripe=select_stripe(),
    )
    return BusinessOperator(
        agent=agent,
        stripe=agent.stripe,
        starting_balance=starting_balance,
        scout=ProcurementScout(demo_catalog()),
    )


def test_sourced_spend_within_margin_is_approved_and_paid() -> None:
    op = _operator_with_scout()
    # Job needs image_generation; scout sources the £20 basic. Margin-safe budget
    # is 140 - 60 = 80, so £20 is comfortably approved.
    job = Job(
        job_id="job_img",
        title="50 banners",
        revenue=140.0,
        protected_margin=60.0,
        customer_id="cust_a",
        invoice_ref="inv_img",
        tools=(ToolPurchase(name="image_tool", category="design_assets", cost=999.0,
                            capability="image_generation"),),
    )
    outcome = op.run_job(job)
    assert outcome.revenue_booked
    paid = [s for s in outcome.spends if s.paid]
    assert len(paid) == 1
    # The catalog's canonical £20 price was used, NOT the £999 placeholder cost.
    assert paid[0].tool.cost == 20.0
    assert outcome.cost_spent == 20.0
    assert outcome.margin_kept == 120.0
    assert outcome.margin_protected


def test_RED_sourced_spend_that_kills_margin_is_refused_by_rules() -> None:
    # The structural safety claim: even a sourced (scout-chosen, canonical-price)
    # spend cannot escape the over-budget rule. The job's margin-safe headroom is
    # tiny; the cheapest qualifying compute tool (£22) exceeds it -> REFUSED.
    op = _operator_with_scout()
    job = Job(
        job_id="job_thin",
        title="Thin-margin compute job",
        revenue=30.0,
        protected_margin=15.0,  # margin-safe budget = 30 - 15 = 15; cheapest compute is £22
        customer_id="cust_b",
        invoice_ref="inv_thin",
        tools=(ToolPurchase(name="compute_tool", category="compute", cost=1.0,
                            capability="compute"),),
    )
    outcome = op.run_job(job)
    assert outcome.revenue_booked
    refused = [s for s in outcome.spends if not s.paid]
    assert len(refused) == 1
    assert refused[0].decision == DecisionKind.BLOCK.value
    assert "self_spend_over_budget" in refused[0].reason or refused[0].status.value.startswith("refused")
    # Money must not have moved, and margin is protected by the refusal.
    assert outcome.cost_spent == 0.0
    assert outcome.margin_protected


def test_sourcing_savings_surface_on_rollup() -> None:
    op = _operator_with_scout()
    job = Job(
        job_id="job_img2",
        title="Banner set",
        revenue=140.0,
        protected_margin=60.0,
        customer_id="cust_c",
        invoice_ref="inv_img2",
        tools=(ToolPurchase(name="image_tool", category="design_assets", cost=999.0,
                            capability="image_generation"),),
    )
    op.run_job(job)
    rollup = op.rollup.as_dict()
    assert rollup["sourcing_savings"] == 25.0
    assert len(rollup["sourcings"]) == 1
    assert rollup["sourcings"][0]["chosen"]["item_id"] == "img_basic"


def test_pre_f3_job_without_capability_is_unchanged() -> None:
    # A tool with no capability and no scout-substitution runs verbatim — the
    # pre-F3 contract every existing job/test relies on.
    op = _operator_with_scout()
    job = Job(
        job_id="job_fixed",
        title="Fixed tool job",
        revenue=100.0,
        protected_margin=40.0,
        customer_id="cust_d",
        invoice_ref="inv_fixed",
        tools=(ToolPurchase(name="fixed_api", category="api_credits", cost=25.0),),
    )
    outcome = op.run_job(job)
    paid = [s for s in outcome.spends if s.paid]
    assert len(paid) == 1
    assert paid[0].tool.cost == 25.0  # used verbatim, no sourcing
    assert op.rollup.as_dict()["sourcing_savings"] == 0.0
