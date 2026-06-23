"""Autonomous business-operator loop — Arbiter as a money-operator for a service business.

This is the product the demo shows: an agent that runs the financial back office
of a developer-for-hire / shop / freelancer. For each paid job it:

  1. EARN     — a client pays an invoice (Stripe test-mode checkout webhook).
  2. VERIFY   — the invoice is run through the *existing* policy engine
                (duplicate / amount-mismatch / fraud) before revenue is booked.
  3. READ JOB — the agent determines which tools it must buy to deliver the job.
  4. BUDGET   — each tool spend is budgeted against THAT invoice: the per-job
                margin-safe headroom is ``revenue - protected_margin - already_spent``.
  5. SPEND/REFUSE — the spend runs through the SAME 3-layer agent. The existing
                ``_self_spend_over_budget`` / ``_self_spend_off_goal`` rules are the
                hard gate. A spend that would eat the margin is REFUSED — the
                signature beat — and a real Nemotron call (the spend-judge) narrates
                the reasoning live. Approved spend pays out via Stripe test-mode.
  6. LEDGER   — per-job: revenue in, cost out, margin kept, waste blocked.

The whole loop is *orchestration on top of* the existing engine. It never edits
``policy/rules.py`` or ``agent/agent.py``: margin protection is achieved purely by
feeding the per-job margin-safe headroom into ``PolicyContext.budget_remaining``,
which the existing over-budget rule already enforces. That is the design: the
operator re-points the guard rules from "don't overspend a global cap" to "don't
overspend THIS job's margin," with no change to the rules themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .agent import ArbiterAgent
from .agent.spend_judge import MockSpendJudge, SpendJudge, SpendJudgement
from .ledger import EventLedger
from .models import (
    AgentEvent,
    DecisionKind,
    EventKind,
    PolicyContext,
    PolicyResult,
    SpendContext,
)
from .stripe_glue import StripeGlue

# Categories of tool the operator may buy to *deliver* client work. Distinct from
# the agent's reinvest capabilities (fraud_detection / ocr / bank_reconciliation):
# delivery spend buys inputs for a job; reinvest spend upgrades the agent itself.
# Both are just ``PolicyContext.allowed_categories`` config — no rule knows the
# difference, which is exactly why no rule had to change.
DELIVERY_CATEGORIES: frozenset[str] = frozenset(
    {"api_credits", "compute", "hosting", "design_assets", "data"}
)

# A refused spend that the owner is shown for confirmation; the hook may block on
# a real phone tap (web demo) or be absent (offline). It never un-refuses the
# spend — the agent's margin protection is final — it drives the human-in-the-loop
# moment and records that the owner saw and confirmed the refusal.
RefusalHook = Callable[[SpendContext, PolicyResult], None]


class SpendStatus(str, Enum):
    PAID = "paid"
    REFUSED_MARGIN = "refused_margin"
    REFUSED_OFFGOAL = "refused_offgoal"
    REFUSED_OTHER = "refused_other"


@dataclass(frozen=True)
class ToolPurchase:
    """A tool the agent considers buying to deliver a job."""

    name: str
    category: str
    cost: float
    rationale: str = ""


@dataclass(frozen=True)
class Job:
    """A paid unit of client work the operator must deliver profitably.

    ``revenue`` is what the client pays (the invoice). ``protected_margin`` is the
    profit floor the operator must keep — the agent may spend on delivery only down
    to ``revenue - protected_margin``. ``invoice_amount`` defaults to ``revenue``;
    set it different to model an amount-mismatch fraud. ``duplicate`` marks an
    invoice whose fingerprint was already seen (replayed payment).
    """

    job_id: str
    title: str
    revenue: float
    protected_margin: float
    tools: tuple[ToolPurchase, ...]
    customer_id: str
    invoice_ref: str
    invoice_amount: Optional[float] = None  # None -> equals revenue (clean)
    duplicate: bool = False  # seed the fingerprint first -> verify rejects it

    @property
    def effective_invoice_amount(self) -> float:
        return self.invoice_amount if self.invoice_amount is not None else self.revenue


@dataclass(frozen=True)
class SpendOutcome:
    """What happened to one tool-buy decision on a job."""

    tool: ToolPurchase
    status: SpendStatus
    decision: str          # the engine's decision value
    reason: str            # the engine's (rules) reason — the hard gate
    margin_safe_budget: float
    judgement: SpendJudgement  # the advisory reasoning narrative (NIM/mock)
    owner_confirmed: bool = False  # owner saw + confirmed a refusal (phone beat)

    @property
    def paid(self) -> bool:
        return self.status == SpendStatus.PAID

    def as_dict(self) -> dict:
        return {
            "tool": self.tool.name,
            "category": self.tool.category,
            "cost": self.tool.cost,
            "status": self.status.value,
            "decision": self.decision,
            "reason": self.reason,
            "margin_safe_budget": round(self.margin_safe_budget, 2),
            "owner_confirmed": self.owner_confirmed,
            "judgement": self.judgement.as_dict(),
        }


@dataclass
class JobOutcome:
    """The per-job ledger line: revenue in, cost out, margin kept, waste blocked."""

    job_id: str
    title: str
    revenue: float
    protected_margin: float
    revenue_booked: bool
    invoice_decision: str
    invoice_reason: str
    spends: list[SpendOutcome] = field(default_factory=list)
    payment_id: Optional[str] = None  # real Stripe pi_.. when the live rail booked it

    @property
    def cost_spent(self) -> float:
        return sum(s.tool.cost for s in self.spends if s.paid)

    @property
    def waste_blocked(self) -> float:
        return sum(s.tool.cost for s in self.spends if not s.paid)

    @property
    def margin_kept(self) -> float:
        """Profit actually retained on this job (0 if revenue was never booked)."""
        if not self.revenue_booked:
            return 0.0
        return self.revenue - self.cost_spent

    @property
    def margin_protected(self) -> bool:
        """True when the kept margin honoured the floor (or revenue was rejected)."""
        if not self.revenue_booked:
            return True
        return self.margin_kept >= self.protected_margin - 1e-9

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "revenue": round(self.revenue, 2),
            "protected_margin": round(self.protected_margin, 2),
            "revenue_booked": self.revenue_booked,
            "invoice_decision": self.invoice_decision,
            "invoice_reason": self.invoice_reason,
            "cost_spent": round(self.cost_spent, 2),
            "waste_blocked": round(self.waste_blocked, 2),
            "margin_kept": round(self.margin_kept, 2),
            "margin_protected": self.margin_protected,
            "payment_id": self.payment_id,
            "spends": [s.as_dict() for s in self.spends],
        }


@dataclass
class BusinessRollup:
    """Aggregate across every job — the dashboard's headline business numbers."""

    starting_balance: float
    jobs: list[JobOutcome] = field(default_factory=list)

    @property
    def revenue_booked(self) -> float:
        return sum(j.revenue for j in self.jobs if j.revenue_booked)

    @property
    def cost_spent(self) -> float:
        return sum(j.cost_spent for j in self.jobs)

    @property
    def waste_blocked(self) -> float:
        return sum(j.waste_blocked for j in self.jobs)

    @property
    def fraud_revenue_rejected(self) -> float:
        return sum(j.revenue for j in self.jobs if not j.revenue_booked)

    @property
    def net_profit(self) -> float:
        return self.revenue_booked - self.cost_spent

    @property
    def balance(self) -> float:
        return self.starting_balance + self.net_profit

    @property
    def all_margins_protected(self) -> bool:
        return all(j.margin_protected for j in self.jobs)

    def as_dict(self) -> dict:
        return {
            "starting_balance": round(self.starting_balance, 2),
            "balance": round(self.balance, 2),
            "revenue_booked": round(self.revenue_booked, 2),
            "cost_spent": round(self.cost_spent, 2),
            "waste_blocked": round(self.waste_blocked, 2),
            "fraud_revenue_rejected": round(self.fraud_revenue_rejected, 2),
            "net_profit": round(self.net_profit, 2),
            "jobs_completed": sum(1 for j in self.jobs if j.revenue_booked),
            "jobs_total": len(self.jobs),
            "all_margins_protected": self.all_margins_protected,
            "jobs": [j.as_dict() for j in self.jobs],
        }


class BusinessOperator:
    """Runs paid jobs through the existing engine, protecting per-job margin.

    Reuses, never rebuilds: invoice verification and tool spend both flow through
    the same ``ArbiterAgent`` and land in the same ``EventLedger`` the dashboard
    polls. The operator's only addition is to set, per spend, the margin-safe
    budget the existing over-budget rule enforces.
    """

    def __init__(
        self,
        agent: ArbiterAgent,
        stripe: StripeGlue,
        spend_judge: Optional[SpendJudge] = None,
        starting_balance: float = 50.0,
        delivery_categories: frozenset[str] = DELIVERY_CATEGORIES,
    ) -> None:
        self.agent = agent
        self.stripe = stripe
        self.spend_judge = spend_judge or MockSpendJudge()
        self.delivery_categories = delivery_categories
        self.rollup = BusinessRollup(starting_balance=starting_balance)

    # --- the loop ------------------------------------------------------------

    def run_job(self, job: Job, on_spend_refused: Optional[RefusalHook] = None) -> JobOutcome:
        """Earn -> verify -> budget -> spend/refuse -> per-job ledger for one job."""
        # 1. EARN — the client pays the invoice. On the live rail this creates a
        # real, confirmed test-mode PaymentIntent (pi_.. succeeded); on the stub it
        # just records the intent. webhook_received models Stripe notifying the agent.
        pay = self.stripe.create_payment(job.invoice_ref, job.revenue, "GBP")
        self.stripe.webhook_received("payment_intent.succeeded", job.invoice_ref)

        # A replayed/duplicate invoice: seed the fingerprint so the existing
        # duplicate rule recognises it on verification (models "already paid once").
        if job.duplicate:
            self.agent.ctx.recent_payment_fingerprints.add(
                (job.customer_id, job.revenue, job.invoice_ref)
            )

        # 2. VERIFY — run the incoming payment through the existing fraud/dup/amount
        # rules before booking revenue. This reuses the engine wholesale.
        invoice_event = AgentEvent(
            kind=EventKind.INVOICE_PAYMENT,
            vendor_id=job.customer_id,
            invoice_id=job.job_id,
            ref=job.invoice_ref,
            amount=job.revenue,
            invoice_amount=job.effective_invoice_amount,
            message=f"Client payment for job: {job.title}",
        )
        inv = self.agent.decide(
            invoice_event,
            event_id=f"{job.job_id}:invoice",
            demo_beat=f"Earn: client pays '{job.title}' (£{job.revenue:.0f})",
        )
        outcome = JobOutcome(
            job_id=job.job_id,
            title=job.title,
            revenue=job.revenue,
            protected_margin=job.protected_margin,
            revenue_booked=(inv.decision == DecisionKind.APPROVE),
            invoice_decision=inv.decision.value,
            invoice_reason=inv.reason,
            payment_id=pay.stripe_id,
        )
        if not outcome.revenue_booked:
            # Bad invoice — revenue refused, no delivery spend. The verify beat.
            self.rollup.jobs.append(outcome)
            return outcome

        # 3/4/5. READ JOB -> BUDGET each tool against THIS invoice -> SPEND or REFUSE.
        spent = 0.0
        for tool in job.tools:
            margin_safe_budget = job.revenue - job.protected_margin - spent
            outcome_spend = self._decide_spend(job, tool, margin_safe_budget, on_spend_refused)
            outcome.spends.append(outcome_spend)
            if outcome_spend.paid:
                spent += tool.cost

        self.rollup.jobs.append(outcome)
        return outcome

    def _decide_spend(
        self,
        job: Job,
        tool: ToolPurchase,
        margin_safe_budget: float,
        on_spend_refused: Optional[RefusalHook],
    ) -> SpendOutcome:
        """Judge + gate one tool buy. Rules are the hard gate; NIM narrates."""
        spend_ctx = SpendContext(
            job_id=job.job_id,
            job_title=job.title,
            revenue=job.revenue,
            protected_margin=job.protected_margin,
            budget_remaining=margin_safe_budget,
            tool_name=tool.name,
            tool_category=tool.category,
            cost=tool.cost,
            allowed_categories=tuple(sorted(self.delivery_categories)),
            tool_rationale=tool.rationale,
        )
        # Advisory reasoning (live NIM or mock) — visible on the dashboard.
        judgement = self.spend_judge.judge_spend(spend_ctx)

        # Hard gate: point the SAME engine's budget at THIS job's margin-safe
        # headroom and route the spend as a SELF_SPEND. The existing
        # over-budget / off-goal rules decide. No rule was changed to do this.
        self.agent.ctx.budget_remaining = margin_safe_budget
        self.agent.ctx.allowed_categories = set(self.delivery_categories)
        spend_event = AgentEvent(
            kind=EventKind.SELF_SPEND,
            amount=tool.cost,
            category=tool.category,
            message=(
                f"Buy '{tool.name}' (£{tool.cost:.0f}) to deliver '{job.title}'. "
                f"{tool.rationale}".strip()
            ),
        )
        result = self.agent.settle(
            spend_event,
            event_id=f"{job.job_id}:spend:{tool.name}",
            demo_beat=f"Spend on '{job.title}': {tool.name} (£{tool.cost:.0f})",
        )

        if result.decision == DecisionKind.APPROVE:
            # Money already moved inside settle() — the single door decides AND
            # executes, so there's no separate pay line that could fire without a
            # matching APPROVE (or be skipped after one).
            return SpendOutcome(
                tool=tool,
                status=SpendStatus.PAID,
                decision=result.decision.value,
                reason=result.reason,
                margin_safe_budget=margin_safe_budget,
                judgement=judgement,
            )

        # Refused. Classify why, for the per-job ledger + the dashboard.
        refs = set(result.policy_refs)
        if "self_spend_over_budget" in refs:
            status = SpendStatus.REFUSED_MARGIN
        elif "self_spend_off_goal" in refs:
            status = SpendStatus.REFUSED_OFFGOAL
        else:
            status = SpendStatus.REFUSED_OTHER

        # Phone beat: show the owner the refusal and let them confirm it. The hook
        # may genuinely block on a real tap (web demo). It never un-refuses the
        # spend — margin protection is the agent's own decision and stands.
        owner_confirmed = False
        if on_spend_refused is not None:
            on_spend_refused(spend_ctx, result.as_policy_result())
            owner_confirmed = True

        return SpendOutcome(
            tool=tool,
            status=status,
            decision=result.decision.value,
            reason=result.reason,
            margin_safe_budget=margin_safe_budget,
            judgement=judgement,
            owner_confirmed=owner_confirmed,
        )

    def run_all(self, jobs: list[Job], on_spend_refused: Optional[RefusalHook] = None) -> BusinessRollup:
        for job in jobs:
            self.run_job(job, on_spend_refused=on_spend_refused)
        return self.rollup


# --- demo job set ------------------------------------------------------------


def demo_jobs() -> list[Job]:
    """The job timeline the demo plays — each job hits a storyboard beat.

    1. Clean profitable delivery (earn -> verify -> on-goal in-budget spend -> profit).
    2. The margin refusal (THE beat): a useful, on-goal tool that simply costs
       more than the job's margin-safe headroom -> the agent refuses its own spend.
    3. A fraud invoice (amount mismatch) -> revenue rejected, never booked.
    4. An off-goal spend attempt (marketing tool) -> refused as not a delivery cost.
    """
    return [
        Job(
            job_id="job_01",
            title="Tide-times API for a surf shop",
            revenue=140.0,
            protected_margin=60.0,
            customer_id="cust_surfshop",
            invoice_ref="inv_1001",
            tools=(
                ToolPurchase(
                    name="tide_api_credits",
                    category="api_credits",
                    cost=30.0,
                    rationale="Marine/tide data API to source the tide times.",
                ),
            ),
        ),
        Job(
            job_id="job_02",
            title="50 product banners for a store",
            revenue=90.0,
            protected_margin=40.0,
            customer_id="cust_store",
            invoice_ref="inv_1002",
            tools=(
                ToolPurchase(
                    name="image_gen_compute",
                    category="compute",
                    cost=35.0,
                    rationale="GPU compute to render the 50 banners.",
                ),
                # On-goal and genuinely useful — but £45 on top of the £35 already
                # spent leaves only £10 on a job that must keep £40. The agent
                # refuses its OWN spend because it would kill the margin.
                ToolPurchase(
                    name="premium_stock_library",
                    category="design_assets",
                    cost=45.0,
                    rationale="Premium stock library for nicer source imagery.",
                ),
            ),
        ),
        Job(
            job_id="job_03",
            title="Logo redesign (suspicious invoice)",
            revenue=200.0,
            protected_margin=80.0,
            customer_id="cust_unknown",
            invoice_ref="inv_1003",
            invoice_amount=150.0,  # invoice says 150 but 200 is being claimed -> mismatch
            tools=(),
        ),
        Job(
            job_id="job_04",
            title="Bug-fix retainer for a SaaS",
            revenue=120.0,
            protected_margin=50.0,
            customer_id="cust_saas",
            invoice_ref="inv_1004",
            tools=(
                ToolPurchase(
                    name="ci_compute_minutes",
                    category="compute",
                    cost=20.0,
                    rationale="CI minutes to run the fix's test matrix.",
                ),
                # Off-goal: a marketing tool is not a delivery cost for a bug-fix.
                ToolPurchase(
                    name="ad_campaign_tool",
                    category="marketing",
                    cost=15.0,
                    rationale="Run ads for the client (not part of the job).",
                ),
            ),
        ),
    ]
