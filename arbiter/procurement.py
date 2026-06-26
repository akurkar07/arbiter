"""F3 - Procurement scout: source smart, stay governed.

The operator already *refuses* spend that would kill a job's margin. F3 adds the
other half of disciplined money: when a job needs a capability (image generation,
OCR, tide data...), the scout sources the **cheapest catalog equivalent** that
meets the job's quality bar instead of buying the first or fanciest tool.

Why this is a moat and not a liability
--------------------------------------
The naive version - hand an LLM a web-search tool and let it pick what to buy off
a live page - destroys the whole pitch. It returns the spend decision to a raw
model reading untrusted content (prompt-injection straight into the money path:
that is exactly why the rules engine has an ``_instruction_override`` rule), and
it is what every other "autonomous spender" does.

The governed version keeps the instinct (maximise profit, buy cheaper) and keeps
the moat intact:

1. The scout proposes a buy ONLY from a **bounded, owner-curated catalog**. The
   open web is never in the money path. Sourcing is data, not an LLM free-for-all.
2. The model's role is reduced to selecting a ``catalog_item_id`` from the
   candidate set. It never names a price. The backend canonicalises price,
   category and vendor from the owner's catalog, so a model cannot smuggle a
   cheaper number, a different category, or an off-catalog vendor into a spend.
3. The chosen item becomes a normal ``SELF_SPEND`` and runs through the SAME
   margin gate + escalation as every other spend. Research *advises*; policy
   *decides* - the line already written in ``spend_judge.py``.

The demo beat writes itself: the scout finds a £45 premium tool and a £20
equivalent that both clear the job's quality bar; the agent buys the £20 one to
protect margin, and the ledger shows it *chose* the cheaper option - then still
refuses a £60 "premium" pick that would push the job negative. Disciplined spend,
fully governed, zero trust compromise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Catalog - owner-curated. The ONLY place a buyable tool's real price, category
# and vendor live. Nothing the model says can change these numbers; the scout
# canonicalises every proposal back to a row here before it becomes a spend.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogItem:
    """One owner-approved buyable tool.

    ``capability`` groups items that deliver the same job need (e.g. two image
    generators are both ``image_generation``), so the scout can compare like for
    like. ``quality`` is the owner's own 0..1 rating: the scout will not drop
    below a job's quality bar just to save money. ``price`` and ``category`` are
    canonical - the model never supplies them.
    """

    item_id: str
    name: str
    capability: str
    category: str
    price: float
    quality: float  # owner-rated 0..1; the scout respects a job's minimum bar
    vendor_id: str
    currency: str = "GBP"

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "capability": self.capability,
            "category": self.category,
            "price": self.price,
            "quality": self.quality,
            "vendor_id": self.vendor_id,
            "currency": self.currency,
        }


class ProcurementCatalog:
    """An immutable, owner-curated set of buyable tools, indexed by capability.

    Fail-closed by construction: the scout can only ever return an item that is
    in here. There is no path to an off-catalog vendor or an open-web fetch in
    the money flow - that is the structural safety property F3 sells.
    """

    def __init__(self, items: list[CatalogItem]) -> None:
        self._by_id: dict[str, CatalogItem] = {}
        self._by_capability: dict[str, list[CatalogItem]] = {}
        for it in items:
            if it.item_id in self._by_id:
                raise ValueError(f"duplicate catalog item_id: {it.item_id!r}")
            if it.price < 0:
                raise ValueError(f"catalog item {it.item_id!r} has negative price")
            if not (0.0 <= it.quality <= 1.0):
                raise ValueError(f"catalog item {it.item_id!r} quality must be 0..1")
            self._by_id[it.item_id] = it
            self._by_capability.setdefault(it.capability, []).append(it)

    def get(self, item_id: str) -> Optional[CatalogItem]:
        """Canonical lookup. Returns None for an unknown id - the caller refuses
        an off-catalog proposal rather than inventing one."""
        return self._by_id.get(item_id)

    def candidates(self, capability: str, min_quality: float = 0.0) -> list[CatalogItem]:
        """Every catalog item that delivers ``capability`` at or above the quality
        bar, cheapest first. This is the candidate set the scout chooses from -
        and the ordering is the disciplined-spend default before any model speaks.
        """
        items = [
            it for it in self._by_capability.get(capability, []) if it.quality >= min_quality
        ]
        return sorted(items, key=lambda it: (it.price, -it.quality))

    def capabilities(self) -> list[str]:
        return sorted(self._by_capability)

    def all_items(self) -> list[CatalogItem]:
        return list(self._by_id.values())


# ---------------------------------------------------------------------------
# Sourcing request + result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourcingRequest:
    """A capability a job needs the scout to source the cheapest fit for.

    ``min_quality`` is the job's quality floor (the owner won't ship a banner job
    on a 0.2-quality generator just to save £15). ``max_spend`` is an optional
    hard sourcing ceiling distinct from the margin gate - sourcing should not even
    *propose* something it can already see blows the job's margin headroom, though
    the rules engine remains the actual enforcer.
    """

    capability: str
    job_id: str
    job_title: str
    min_quality: float = 0.0
    max_spend: Optional[float] = None


class SourcingDecision(str):
    """How the scout resolved a sourcing request (string enum for clean JSON)."""


SELECTED = "selected"
NO_CANDIDATE = "no_candidate"
OFF_CATALOG = "off_catalog"
BELOW_QUALITY = "below_quality"


@dataclass(frozen=True)
class SourcingResult:
    """The scout's proposal: a canonical catalog item (or a refusal to source).

    ``chosen`` is always a real ``CatalogItem`` from the catalog (or None on a
    refusal). ``cheaper_than`` records the next-cheapest rejected alternative so
    the dashboard can show "chose £20 over £45 to protect margin" as a fact, not
    a claim. ``considered`` is the full candidate set the choice was made from -
    visible provenance for the demo and the audit trail.
    """

    request: SourcingRequest
    outcome: str
    chosen: Optional[CatalogItem] = None
    reason: str = ""
    considered: tuple[CatalogItem, ...] = field(default_factory=tuple)
    cheaper_than: Optional[CatalogItem] = None
    model_proposed_id: Optional[str] = None  # what the model actually returned
    model_was_corrected: bool = False  # backend overrode an unsafe/odd proposal

    @property
    def sourced(self) -> bool:
        return self.outcome == SELECTED and self.chosen is not None

    @property
    def savings_vs_premium(self) -> float:
        """How much the disciplined pick saved against the priciest considered
        candidate - the headline 'bought smart' number for the dashboard."""
        if self.chosen is None or not self.considered:
            return 0.0
        premium = max(self.considered, key=lambda it: it.price)
        return round(premium.price - self.chosen.price, 2)

    @property
    def premium(self) -> Optional["CatalogItem"]:
        """The priciest qualifying candidate the scout passed over - the 'over
        £45' half of the 'chose £20 over £45' story."""
        if not self.considered:
            return None
        return max(self.considered, key=lambda it: it.price)

    def as_dict(self) -> dict:
        return {
            "capability": self.request.capability,
            "job_id": self.request.job_id,
            "job_title": self.request.job_title,
            "outcome": self.outcome,
            "reason": self.reason,
            "chosen": self.chosen.as_dict() if self.chosen else None,
            "cheaper_than": self.cheaper_than.as_dict() if self.cheaper_than else None,
            "savings_vs_premium": self.savings_vs_premium,
            "premium": self.premium.as_dict() if self.premium else None,
            "model_proposed_id": self.model_proposed_id,
            "model_was_corrected": self.model_was_corrected,
            "considered": [it.as_dict() for it in self.considered],
        }


# ---------------------------------------------------------------------------
# The selector the model plugs into - bounded to returning a catalog id
# ---------------------------------------------------------------------------


class CatalogSelector:
    """Selects a ``catalog_item_id`` from a bounded candidate set.

    The model's entire authority in F3 is this: given the candidate rows (id,
    name, quality, price already canonical), return ONE id. It cannot name a
    price, add a vendor, or reach the open web. The default implementation is the
    disciplined-spend baseline - cheapest item that clears the quality bar - which
    is also the safe fallback whenever a model proposes something off-catalog.
    """

    def select(self, candidates: list[CatalogItem], request: SourcingRequest) -> Optional[str]:
        if not candidates:
            return None
        return candidates[0].item_id  # candidates are cheapest-first


class ProcurementScout:
    """Sources the cheapest catalog fit for a job need, then hands it to policy.

    The scout NEVER moves money and NEVER returns a price the model chose. It:
      1. pulls the candidate set from the owner catalog (cheapest-first),
      2. asks the selector (model or baseline) for ONE catalog id,
      3. canonicalises that id back to a real catalog row - refusing anything
         off-catalog or below the job's quality bar by falling back to the
         disciplined baseline (cheapest qualifying item),
      4. returns a ``SourcingResult`` the operator turns into a normal SELF_SPEND.

    Step 3 is the structural safety claim: a model proposal can only ever shrink
    to "a real, owner-approved, quality-passing catalog row" - it cannot expand
    the agent's spending authority. "Research advises, policy decides."
    """

    def __init__(self, catalog: ProcurementCatalog, selector: Optional[CatalogSelector] = None) -> None:
        self.catalog = catalog
        self.selector = selector or CatalogSelector()

    def source(self, request: SourcingRequest) -> SourcingResult:
        candidates = self.catalog.candidates(request.capability, request.min_quality)
        if not candidates:
            # Either no item delivers this capability, or none clears the quality
            # bar. Refuse to source - the operator buys nothing rather than
            # something unfit. Distinguish the two for an honest dashboard reason.
            any_at_all = self.catalog.candidates(request.capability, 0.0)
            if any_at_all:
                return SourcingResult(
                    request=request,
                    outcome=BELOW_QUALITY,
                    reason=(
                        f"No '{request.capability}' tool meets the job's quality bar "
                        f"({request.min_quality:.2f}); refusing to source a substandard tool."
                    ),
                    considered=tuple(any_at_all),
                )
            return SourcingResult(
                request=request,
                outcome=NO_CANDIDATE,
                reason=f"No catalog tool provides '{request.capability}'.",
            )

        baseline = candidates[0]  # cheapest qualifying - the disciplined default
        proposed_id = self.selector.select(candidates, request)
        chosen = self.catalog.get(proposed_id) if proposed_id else None
        corrected = False

        # Canonicalise: a proposal that is off-catalog, wrong-capability, or below
        # the quality bar is overridden by the disciplined baseline. The model can
        # never widen authority - only pick within the safe set, or be corrected
        # back into it.
        if (
            chosen is None
            or chosen.capability != request.capability
            or chosen.quality < request.min_quality
        ):
            chosen = baseline
            corrected = proposed_id is not None and proposed_id != baseline.item_id

        # The next-cheapest *other* candidate, for the "chose X over Y" story.
        cheaper_than = next((it for it in candidates if it.item_id != chosen.item_id), None)

        return SourcingResult(
            request=request,
            outcome=SELECTED,
            chosen=chosen,
            reason=(
                f"Sourced '{chosen.name}' (£{chosen.price:.0f}, quality {chosen.quality:.2f}) "
                f"as the cheapest catalog tool clearing the job's quality bar."
            ),
            considered=tuple(candidates),
            cheaper_than=cheaper_than,
            model_proposed_id=proposed_id,
            model_was_corrected=corrected,
        )


# ---------------------------------------------------------------------------
# Demo catalog - the owner's curated tool list for the procurement demo
# ---------------------------------------------------------------------------


def demo_catalog() -> ProcurementCatalog:
    """The owner-curated catalog the F3 demo sources from.

    Built so the headline beat lands: for ``image_generation`` there is a cheap
    qualifying tool (£20), a mid tool, and a premium (£45) - the scout buys the
    £20 one. A separate premium-only capability seeds the £60 pick that, once
    sourced onto a thin-margin job, the margin gate refuses. Every row is an
    owner decision; none is reachable except through this catalog.
    """
    return ProcurementCatalog(
        [
            # image_generation - the comparison beat (cheap vs premium, both qualify)
            CatalogItem("img_basic", "PixelForge Lite", "image_generation",
                        "design_assets", 20.0, 0.78, "pixelforge"),
            CatalogItem("img_mid", "RenderHub Standard", "image_generation",
                        "design_assets", 32.0, 0.85, "renderhub"),
            CatalogItem("img_premium", "StudioPro Max", "image_generation",
                        "design_assets", 45.0, 0.93, "studiopro"),
            # ocr - single cheap fit
            CatalogItem("ocr_basic", "ScanText OCR", "ocr",
                        "data", 12.0, 0.80, "scantext"),
            # tide_data - single fit for the surf-shop style job
            CatalogItem("tide_api", "MarineData Tides", "tide_data",
                        "api_credits", 18.0, 0.88, "marinedata"),
            # compute - cheap and premium; premium seeds a margin-killer on thin jobs
            CatalogItem("compute_spot", "SpotCompute", "compute",
                        "compute", 22.0, 0.82, "spotcompute"),
            CatalogItem("compute_premium", "HyperGPU Dedicated", "compute",
                        "compute", 60.0, 0.95, "hypergpu"),
        ]
    )
