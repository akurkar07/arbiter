"""Tests for the operator->timeline enrichment that drives the dashboard.

Alex's dashboard groups the per-job margin ledger on each timeline row's ``job``
field and spotlights the signature beat when a row's ``margin_killer`` is True.
The operator owns both facts (the job title, and *why* a spend was refused) but
they only reach the dashboard if the operator stamps them onto the ledger row.

These lock that contract:
* every operator-produced row (earn + each spend) carries the job title in ``job``;
* ONLY a margin refusal carries ``margin_killer=True`` — an off-goal refusal does
  not, so the dashboard can tell the two refusals apart instead of mislabelling
  an off-goal block as a margin kill;
* the enrichment lives in the operator + ledger, never in the agent core: a row
  produced by a plain ``agent.decide`` (no operator) has ``job=None`` and
  ``margin_killer=False``.
"""

from __future__ import annotations

from arbiter.agent import ArbiterAgent, ConsoleEscalation
from arbiter.agent.spend_judge import MockSpendJudge
from arbiter.ledger import EventLedger
from arbiter.models import AgentEvent, EventKind, PolicyContext
from arbiter.operator import BusinessOperator, Job, ToolPurchase
from arbiter.stripe_glue import StripeGlue


def _operator(starting_balance: float = 50.0) -> BusinessOperator:
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
    )


def _rows_by_id(op: BusinessOperator) -> dict[str, dict]:
    return {r["id"]: r for r in op.agent.ledger.as_timeline()}


def test_timeline_rows_carry_job_title() -> None:
    """Earn + spend rows for a job are tagged with that job's title, so the
    dashboard groups them under the job instead of a single 'Operations' bucket."""
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
    op.run_job(job)
    rows = _rows_by_id(op)
    # the earn (invoice) row and the spend row both name the job
    assert rows["j1:invoice"]["job"] == "Tide API"
    assert rows["j1:spend:tide_api"]["job"] == "Tide API"


def test_margin_refusal_row_is_flagged_margin_killer() -> None:
    """THE beat: the margin-refused spend row carries margin_killer=True; the
    paid spend on the same job does not."""
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
    op.run_job(job)
    rows = _rows_by_id(op)
    assert rows["j2:spend:premium_stock"]["margin_killer"] is True
    assert rows["j2:spend:compute"]["margin_killer"] is False


def test_off_goal_refusal_is_not_a_margin_killer() -> None:
    """An off-goal refusal is a block too, but it must NOT be flagged as a margin
    kill — otherwise the dashboard spotlights the wrong row."""
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
    op.run_job(job)
    rows = _rows_by_id(op)
    # the ad tool is refused (off-goal) but is NOT a margin killer
    assert rows["j5:spend:ad_tool"]["decision"] == "block"
    assert rows["j5:spend:ad_tool"]["margin_killer"] is False


def test_plain_agent_rows_have_no_job_and_no_margin_killer() -> None:
    """A row produced without the operator (a bare agent.decide) defaults cleanly:
    job=None, margin_killer=False. The enrichment is operator-only; the agent core
    knows nothing about jobs or margins."""
    agent = ArbiterAgent(
        ctx=PolicyContext(),
        ledger=EventLedger(),
        escalation=ConsoleEscalation(auto=True),
    )
    agent.decide(
        AgentEvent(kind=EventKind.SELF_SPEND, amount=10.0, category="fraud_detection"),
        event_id="bare:1",
    )
    row = agent.ledger.as_timeline()[0]
    assert row["job"] is None
    assert row["margin_killer"] is False
