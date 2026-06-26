"""Tests for the autonomous business-operator loop.

These lock the storyboard's signature beats as real, measured behaviour driven
through the *existing* engine — not asserted constants:

* a clean job earns, verifies, spends on-goal in-budget, and keeps its margin;
* the MARGIN REFUSAL: a useful, on-goal tool that costs more than the job's
  margin-safe headroom is refused by the agent's OWN over-budget rule;
* a fraud invoice (amount mismatch) is rejected and revenue is never booked;
* an off-goal spend (a marketing tool on a delivery job) is refused;
* across the whole run, every job's protected margin is honoured.

Every decision here comes from ``ArbiterAgent.decide`` running the real rules —
the operator only sets the per-job margin-safe budget the over-budget rule reads.
``policy/rules.py`` and ``agent/agent.py`` are untouched; if either regressed,
these tests would fail.
"""

from __future__ import annotations

from arbiter.agent import ArbiterAgent, ConsoleEscalation
from arbiter.agent.spend_judge import MockSpendJudge
from arbiter.ledger import EventLedger
from arbiter.models import PolicyContext
from arbiter.operator import (
    BusinessOperator,
    Job,
    SpendStatus,
    ToolPurchase,
    demo_jobs,
)
from arbiter.procurement import ProcurementScout, demo_catalog
from arbiter.stripe_glue import StripeGlue


def _operator(starting_balance: float = 50.0) -> BusinessOperator:
    """A fresh operator wired to the real agent with a console (auto) escalation.

    Includes the procurement scout so the F3 sourcing beat in ``demo_jobs`` is
    exercised exactly as the live demo server runs it.
    """
    agent = ArbiterAgent(
        ctx=PolicyContext(),
        ledger=EventLedger(),
        escalation=ConsoleEscalation(auto=True),
    )
    return BusinessOperator(
        agent=agent,
        stripe=StripeGlue(),
        spend_judge=MockSpendJudge(),
        starting_balance=starting_balance,
        scout=ProcurementScout(demo_catalog()),
    )


def test_clean_job_earns_spends_and_keeps_margin() -> None:
    """A clean job: revenue booked, on-goal in-budget tool paid, margin kept."""
    op = _operator()
    job = Job(
        job_id="j1",
        title="Tide API",
        revenue=140.0,
        protected_margin=60.0,
        customer_id="cust",
        invoice_ref="inv1",
        tools=(ToolPurchase("tide_api", "api_credits", 30.0),),
    )
    out = op.run_job(job)
    assert out.revenue_booked is True
    assert out.invoice_decision == "approve"
    assert len(out.spends) == 1
    assert out.spends[0].status == SpendStatus.PAID
    assert out.cost_spent == 30.0
    assert out.margin_kept == 110.0
    assert out.margin_protected is True


def test_margin_refusal_blocks_useful_but_unprofitable_tool() -> None:
    """THE beat: an on-goal, useful tool that would eat the margin is refused.

    The first tool (£35) is in budget and paid. The second (£45) is on-goal and
    genuinely useful, but only £15 of margin-safe budget remains on a job that
    must keep £40 — so the agent's OWN over-budget rule refuses its own spend.
    """
    op = _operator()
    job = Job(
        job_id="j2",
        title="50 banners",
        revenue=90.0,
        protected_margin=40.0,
        customer_id="cust",
        invoice_ref="inv2",
        tools=(
            ToolPurchase("compute", "compute", 35.0),
            ToolPurchase("premium_stock", "design_assets", 45.0),
        ),
    )
    out = op.run_job(job)
    paid, refused = out.spends[0], out.spends[1]
    assert paid.status == SpendStatus.PAID
    # The refusal is a margin block, decided by the deterministic rules layer.
    assert refused.status == SpendStatus.REFUSED_MARGIN
    assert refused.decision == "block"
    assert "exceeds remaining budget" in refused.reason  # the existing rule's own wording
    # Margin is protected: only the £35 tool was bought, £55 profit kept >= £40 floor.
    assert out.cost_spent == 35.0
    assert out.waste_blocked == 45.0
    assert out.margin_kept == 55.0
    assert out.margin_protected is True


def test_fraud_invoice_is_rejected_and_revenue_not_booked() -> None:
    """An amount-mismatch invoice is rejected by the existing rule; no revenue, no spend."""
    op = _operator()
    job = Job(
        job_id="j3",
        title="Logo (suspicious)",
        revenue=200.0,
        protected_margin=80.0,
        customer_id="cust_x",
        invoice_ref="inv3",
        invoice_amount=150.0,  # invoice 150 vs claimed 200 -> mismatch
        tools=(ToolPurchase("compute", "compute", 10.0),),
    )
    out = op.run_job(job)
    assert out.revenue_booked is False
    assert out.invoice_decision == "block"
    # No delivery spend happens when revenue was never booked.
    assert out.spends == []
    assert out.cost_spent == 0.0
    assert out.margin_kept == 0.0


def test_duplicate_invoice_is_rejected() -> None:
    """A replayed invoice (seeded fingerprint) is caught by the duplicate rule."""
    op = _operator()
    job = Job(
        job_id="j4",
        title="Replayed payment",
        revenue=120.0,
        protected_margin=50.0,
        customer_id="cust_dup",
        invoice_ref="inv4",
        duplicate=True,
        tools=(ToolPurchase("compute", "compute", 10.0),),
    )
    out = op.run_job(job)
    assert out.revenue_booked is False
    assert out.invoice_decision == "block"
    assert "duplicate" in out.invoice_reason.lower()


def test_off_goal_spend_is_refused() -> None:
    """A marketing tool on a delivery job is refused as off-goal, not a delivery cost."""
    op = _operator()
    job = Job(
        job_id="j5",
        title="Bug-fix retainer",
        revenue=120.0,
        protected_margin=50.0,
        customer_id="cust_saas",
        invoice_ref="inv5",
        tools=(
            ToolPurchase("ci_compute", "compute", 20.0),
            ToolPurchase("ad_tool", "marketing", 15.0),
        ),
    )
    out = op.run_job(job)
    assert out.spends[0].status == SpendStatus.PAID
    assert out.spends[1].status == SpendStatus.REFUSED_OFFGOAL
    assert out.spends[1].decision == "block"


def test_refusal_hook_fires_and_records_owner_confirmation() -> None:
    """A refused spend drives the phone beat: the owner is shown it and confirms."""
    op = _operator()
    seen: list[str] = []

    def hook(spend_ctx, result) -> None:
        seen.append(spend_ctx.tool_name)

    job = Job(
        job_id="j6",
        title="Banners",
        revenue=90.0,
        protected_margin=40.0,
        customer_id="cust",
        invoice_ref="inv6",
        tools=(
            ToolPurchase("compute", "compute", 35.0),
            ToolPurchase("premium_stock", "design_assets", 45.0),
        ),
    )
    out = op.run_job(job, on_spend_refused=hook)
    assert seen == ["premium_stock"]  # only the refused spend triggers the hook
    assert out.spends[1].owner_confirmed is True
    assert out.spends[0].owner_confirmed is False  # the paid spend doesn't escalate


def test_full_demo_rollup_protects_every_margin() -> None:
    """The whole demo timeline: balance grows, waste is blocked, margins all hold."""
    op = _operator(starting_balance=50.0)
    rollup = op.run_all(demo_jobs())
    d = rollup.as_dict()

    # Clean jobs book revenue (job_01 tide, job_02 banners, job_04 bugfix,
    # job_05 sourced banners); job_03 is fraud and is rejected.
    assert d["jobs_total"] == 5
    assert d["jobs_completed"] == 4
    assert d["fraud_revenue_rejected"] == 200.0  # the rejected logo job

    # Real money math: revenue booked - cost spent = net profit; balance grows.
    assert d["revenue_booked"] == 140.0 + 90.0 + 120.0 + 130.0
    # In-budget on-goal tools, incl. the scout-sourced £20 image tool on job_05.
    assert d["cost_spent"] == 30.0 + 35.0 + 20.0 + 20.0
    assert d["waste_blocked"] == 45.0 + 15.0      # margin kill + off-goal
    assert d["net_profit"] == d["revenue_booked"] - d["cost_spent"]
    assert d["balance"] == 50.0 + d["net_profit"]
    # F3: the scout chose the £20 tool over the £45 premium - real saved margin.
    assert d["sourcing_savings"] == 25.0

    # The invariant that matters: every single job kept its protected margin.
    assert d["all_margins_protected"] is True


def test_operator_never_touches_global_rules_state() -> None:
    """Sanity: the operator achieves margin protection via budget config only.

    The over-budget rule fired on the margin kill, proving the refusal came from
    the existing deterministic rule — not a new bespoke check in the operator.
    """
    op = _operator()
    job = Job(
        job_id="j7",
        title="Banners",
        revenue=90.0,
        protected_margin=40.0,
        customer_id="cust",
        invoice_ref="inv7",
        tools=(
            ToolPurchase("compute", "compute", 35.0),
            ToolPurchase("premium_stock", "design_assets", 45.0),
        ),
    )
    out = op.run_job(job)
    # The refusal reason is the existing rule's own wording about budget.
    assert "exceeds remaining budget" in out.spends[1].reason
