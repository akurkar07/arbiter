/* Arbiter dashboard
 * Reads the state contract from 04_integration_contract.md.
 * Surface Alex owns: render /state, show approval card, post to approve/deny URLs.
 * NO decision logic lives here — the core owns every decision. The detail
 * "trace" is a presentation-only reorganisation of fields already on each row.
 */

const SAMPLE_URL = "sample_state.json";
const SAMPLE_PENDING_URL = "sample_state_pending.json";
const LIVE_URL = "/state";
const POLL_MS = 1500;

// Fallbacks for the seed/goal header before any state has loaded.
const SEED_DEFAULT = 50;
const GOAL_DEFAULT = 500;

const el = {
  // sidebar / chrome
  statusPill: document.getElementById("status-pill"),
  sourcePill: document.getElementById("source-pill"),
  crumbs: document.getElementById("crumbs"),
  runDemo: document.getElementById("run-demo"),
  toggleLive: document.getElementById("toggle-live"),
  // views
  viewOverview: document.getElementById("view-overview"),
  viewDetail: document.getElementById("view-detail"),
  // stats
  earnings: document.getElementById("m-earnings"),
  spend: document.getElementById("m-spend"),
  net: document.getElementById("m-net"),
  margin: document.getElementById("m-margin"),
  catch: document.getElementById("m-catch"),
  catchBar: document.getElementById("m-catch-bar"),
  // business header (seed → goal → balance)
  bhBalance: document.getElementById("bh-balance"),
  bhBal: document.querySelector(".bh-bal"),
  bhSeed: document.getElementById("bh-seed"),
  bhGoal: document.getElementById("bh-goal"),
  bhGoalPct: document.getElementById("bh-goal-pct"),
  bhProgress: document.getElementById("bh-progress"),
  spark: document.getElementById("balance-spark"),
  // per-job ledger
  jobs: document.getElementById("jobs"),
  cApprove: document.getElementById("c-approve"),
  cBlock: document.getElementById("c-block"),
  cEscalate: document.getElementById("c-escalate"),
  pcRules: document.getElementById("pc-rules"),
  pcModel: document.getElementById("pc-model"),
  pcOwner: document.getElementById("pc-owner"),
  stageRules: document.getElementById("stage-rules"),
  stageModel: document.getElementById("stage-model"),
  stageOwner: document.getElementById("stage-owner"),
  // spotlight
  spotlight: document.getElementById("spotlight"),
  spStatus: document.getElementById("sp-status"),
  spIcon: document.getElementById("sp-icon"),
  spBeat: document.getElementById("sp-beat"),
  spSource: document.getElementById("sp-source"),
  spAmount: document.getElementById("sp-amount"),
  spVerdict: document.getElementById("sp-verdict"),
  // approval card
  card: document.getElementById("approval-card"),
  cardBeat: document.getElementById("approval-beat"),
  cardReason: document.getElementById("approval-reason"),
  cardRisk: document.getElementById("approval-risk"),
  cardWait: document.getElementById("approval-wait"),
  btnApprove: document.getElementById("btn-approve"),
  btnDeny: document.getElementById("btn-deny"),
  // table
  decisions: document.getElementById("decisions"),
  logCount: document.getElementById("log-count"),
  // detail
  detailHead: document.getElementById("detail-head"),
  detailEvent: document.getElementById("detail-event"),
  detailTrace: document.getElementById("detail-trace"),
  detailOutcome: document.getElementById("detail-outcome"),
  // end card
  endCard: document.getElementById("end-card"),
  endCardClose: document.getElementById("end-card-close"),
  endStat: document.getElementById("end-stat"),
};

let mode = "sample"; // "sample" | "live"
let pollTimer = null;
let pendingApproval = null;
let currentState = { timeline: [] };
let lastTableKey = "";
let ownerTapResolver = null; // set while the demo waits for a human tap

/* ---------- domain mapping (presentation only) ---------- */

const ALLOWED_SELF = ["fraud_detection", "ocr", "bank_reconciliation"];

// Which engine layer decided this event -> a pipeline stage.
function stageOf(layer) {
  if (layer === "llm" || layer === "model") return "model";
  if (layer === "escalate" || layer === "owner") return "owner";
  return "rules";
}

const STAGE_META = {
  rules: { name: "Policy engine", tech: "Deterministic rules", elKey: "stageRules", countKey: "pcRules" },
  model: { name: "NVIDIA Nemotron", tech: "Bounded judgment", elKey: "stageModel", countKey: "pcModel" },
  owner: { name: "Owner approval", tech: "Phone tap", elKey: "stageOwner", countKey: "pcOwner" },
};

// Human-readable names for the rule/signal refs on each row.
const REF_LABEL = {
  // service-business signals
  invoice_verified: "Invoice verified & reconciled",
  spend_within_budget: "Within the job's budget",
  spend_kills_margin: "Margin-protection guard",
  thin_margin: "Thin-margin review",
  spend_allowed: "On-goal capability",
  // legacy payment-ops signals (still rendered if the core emits them)
  invoice_normal_paid: "Invoice reconciled",
  duplicate_invoice: "Duplicate-payment guard",
  amount_mismatch: "Amount-mismatch guard",
  vendor_detail_change_known_vendor: "Known-vendor bank-change review",
  vendor_detail_change_new_vendor: "New-vendor impersonation guard",
  instruction_override: "Prompt-injection guard",
  new_vendor_small_amount: "New-vendor low-value check",
  self_spend_over_budget: "Budget-ceiling guard",
  self_spend_off_goal: "Off-goal spend guard",
  self_spend_allowed: "Approved reinvestment",
  llm_refine: "Bounded model judgment",
  phone_escalation: "Owner phone approval",
};

function refLabel(ref) {
  return REF_LABEL[ref] || ref;
}

// Inline SVG glyphs per event kind (currentColor).
const ICON = {
  invoice_payment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/></svg>',
  vendor_payment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg>',
  vendor_detail_change: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
  self_spend: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/><circle cx="12" cy="12" r="2"/></svg>',
};

const KIND_LABEL = {
  invoice_payment: "Client payment",
  vendor_payment: "Vendor payment",
  vendor_detail_change: "Vendor detail change",
  self_spend: "Spend to deliver",
};

function iconFor(kind) {
  return ICON[kind] || ICON.vendor_payment;
}

function sourceLine(row) {
  if (row.kind === "invoice_payment" && row.decision === "approve") {
    return "Stripe · client payment received & verified";
  }
  const s = STAGE_META[stageOf(row.layer)];
  return `${s.name} · ${s.tech}`;
}

function isHero(row) {
  // The pitch frame: the agent refusing a spend that would kill the job's margin.
  return (
    (row.margin_killer === true) ||
    (row.kind === "self_spend" && row.decision === "block" && stageOf(row.layer) === "rules")
  );
}

/* ---------- formatting ---------- */

const CURRENCY_SYMBOL = { GBP: "£", USD: "$", EUR: "€" };

function money(amount, currency = "GBP") {
  if (amount == null) return "-";
  const sym = CURRENCY_SYMBOL[currency] || "";
  return sym + Number(amount).toFixed(2);
}

// Signed money for the live feed: client invoices are money in (+),
// spends to deliver are money out (−).
function moneyFlow(row) {
  if (row.amount == null) return { text: "-", cls: "" };
  const sym = CURRENCY_SYMBOL[row.currency] || "£";
  const isIn = row.kind === "invoice_payment";
  const sign = isIn ? "+" : "\u2212";
  return { text: `${sign}${sym}${Number(row.amount).toFixed(2)}`, cls: isIn ? "flow-in" : "flow-out" };
}

function pct(x) {
  return Math.round((Number(x) || 0) * 100) + "%";
}

function riskChip(risk) {
  const v = Number(risk) || 0;
  const level = v >= 0.66 ? "high" : v >= 0.33 ? "med" : "low";
  return `<span class="risk-chip risk-${level}">${Math.round(v * 100)}%</span>`;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function verdictColor(decision) {
  if (decision === "approve") return "var(--approve)";
  if (decision === "block") return "var(--block)";
  return "var(--escalate)";
}

function verdictGlyph(decision) {
  if (decision === "approve") return "✓";
  if (decision === "block") return "✕";
  return "!";
}

/* ---------- stats + pipeline ---------- */

function renderMeters(state) {
  const seed = state.seed != null ? Number(state.seed) : SEED_DEFAULT;
  const goal = state.goal != null ? Number(state.goal) : GOAL_DEFAULT;
  const earnings = Number(state.earnings) || 0;
  const spend = Number(state.spend) || 0;
  const margin = state.margin != null ? Number(state.margin) : earnings - spend;
  const balance = state.balance != null ? Number(state.balance) : seed + earnings - spend;

  // Stat row: revenue in, cost out, margin kept, and balance (reuses #m-net).
  el.earnings.textContent = money(earnings, "GBP");
  el.spend.textContent = money(spend, "GBP");
  if (el.margin) el.margin.textContent = money(margin, "GBP");
  el.net.textContent = money(balance, "GBP");

  // Fraud-screening capability bar lives in the header now.
  if (el.catch) el.catch.textContent = pct(state.catch_rate);
  if (el.catchBar) el.catchBar.style.width = pct(state.catch_rate);

  renderBusinessHeader(seed, goal, balance);
}

function renderBusinessHeader(seed, goal, balance) {
  if (el.bhBalance) el.bhBalance.textContent = money(balance, "GBP");
  if (el.bhSeed) el.bhSeed.textContent = money(seed, "GBP");
  if (el.bhGoal) el.bhGoal.textContent = money(goal, "GBP");
  const ratio = goal > 0 ? balance / goal : 0;
  if (el.bhGoalPct) el.bhGoalPct.textContent = Math.round(ratio * 100) + "%";
  if (el.bhProgress) el.bhProgress.style.width = Math.max(0, Math.min(1, ratio)) * 100 + "%";
}

/* ---------- balance-over-time sparkline ---------- */

function balanceSeries(state) {
  const seed = state.seed != null ? Number(state.seed) : SEED_DEFAULT;
  const pts = [seed];
  let bal = seed;
  for (const r of state.timeline || []) {
    if (r.decision === "approve" && r.amount) {
      if (r.kind === "invoice_payment") bal += r.amount;
      else if (r.kind === "self_spend") bal -= r.amount;
    }
    pts.push(bal);
  }
  return pts;
}

function renderSpark(state) {
  const svg = el.spark;
  if (!svg) return;
  const W = 320, H = 96, padY = 12;
  const goal = state.goal != null ? Number(state.goal) : GOAL_DEFAULT;
  const seed = state.seed != null ? Number(state.seed) : SEED_DEFAULT;
  const series = balanceSeries(state);

  if (series.length <= 1) {
    svg.innerHTML =
      `<text class="spark-empty" x="${W / 2}" y="${H / 2}" text-anchor="middle" dominant-baseline="middle">Awaiting first event…</text>`;
    return;
  }

  const maxV = Math.max(goal, ...series);
  const minV = Math.min(seed, ...series, 0);
  const spanV = maxV - minV || 1;
  const x = (i) => (i / (series.length - 1)) * W;
  const y = (v) => H - padY - ((v - minV) / spanV) * (H - padY * 2);

  const linePts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const areaPts = `0,${H} ` + linePts + ` ${W},${H}`;
  const goalY = y(goal).toFixed(1);
  const lastX = x(series.length - 1).toFixed(1);
  const lastY = y(series[series.length - 1]).toFixed(1);

  svg.innerHTML =
    `<defs><linearGradient id="sparkfill" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="#2fbf74" stop-opacity="0.45"/>` +
    `<stop offset="1" stop-color="#2fbf74" stop-opacity="0"/></linearGradient></defs>` +
    `<line class="spark-goal-line" x1="0" y1="${goalY}" x2="${W}" y2="${goalY}"/>` +
    `<polygon class="spark-area" fill="url(#sparkfill)" points="${areaPts}"/>` +
    `<polyline class="spark-line" points="${linePts}"/>` +
    `<circle class="spark-dot" cx="${lastX}" cy="${lastY}" r="3.5"/>`;
}

/* ---------- per-job ledger ---------- */

function buildJobs(timeline) {
  const order = [];
  const map = new Map();
  for (const r of timeline || []) {
    const name = r.job || "Operations";
    if (!map.has(name)) {
      map.set(name, { name, revenue: 0, cost: 0, refused: 0 });
      order.push(name);
    }
    const j = map.get(name);
    if (r.decision === "approve" && r.amount) {
      if (r.kind === "invoice_payment") j.revenue += r.amount;
      else if (r.kind === "self_spend") j.cost += r.amount;
    }
    if (r.decision === "block" && r.kind === "self_spend" && r.amount) j.refused += r.amount;
  }
  return order.map((n) => {
    const j = map.get(n);
    j.margin = j.revenue - j.cost;
    return j;
  });
}

function renderJobs(timeline) {
  if (!el.jobs) return;
  const jobs = buildJobs(timeline);
  if (!jobs.length) {
    el.jobs.innerHTML = `<div class="jobs-empty">No jobs yet — waiting for the first client payment.</div>`;
    return;
  }
  el.jobs.innerHTML = jobs
    .map((j) => {
      const marginPct = j.revenue > 0 ? Math.max(0, Math.min(1, j.margin / j.revenue)) : 0;
      const neg = j.margin < 0;
      const sub = j.refused > 0
        ? `${money(j.refused)} refused to protect margin`
        : j.revenue > 0 ? "delivered within budget" : "overhead / reinvest";
      return `
      <div class="job-row${j.refused > 0 ? " is-hero" : ""}">
        <div class="job-name"><span class="jdot"></span><span class="jn-text"><b>${escapeHtml(j.name)}</b><small>${escapeHtml(sub)}</small></span></div>
        <div class="job-fig jf-rev"><span class="jf-label">Revenue</span><span class="jf-val">${money(j.revenue)}</span></div>
        <div class="job-fig jf-cost"><span class="jf-label">Cost</span><span class="jf-val">${money(j.cost)}</span></div>
        <div class="job-fig jf-margin${neg ? " neg" : ""}"><span class="jf-label">Margin</span><span class="jf-val">${money(j.margin)}</span></div>
        <div class="job-bar"><div class="job-bar-fill" style="width:${(marginPct * 100).toFixed(0)}%"></div></div>
      </div>`;
    })
    .join("");
}

function tallyFrom(timeline) {
  const t = { approve: 0, block: 0, escalate: 0, rules: 0, model: 0, owner: 0 };
  for (const r of timeline) {
    if (t[r.decision] != null) t[r.decision]++;
    t[stageOf(r.layer)]++;
  }
  return t;
}

function renderCounters(timeline) {
  const t = tallyFrom(timeline);
  el.cApprove.textContent = t.approve;
  el.cBlock.textContent = t.block;
  el.cEscalate.textContent = t.escalate;
  el.pcRules.textContent = t.rules;
  el.pcModel.textContent = t.model;
  el.pcOwner.textContent = t.owner;
}

function setStageActive(stage) {
  for (const k of ["stageRules", "stageModel", "stageOwner"]) {
    el[k].classList.remove("active");
  }
  if (stage) el[STAGE_META[stage].elKey].classList.add("active");
}

function bump(node) {
  if (!node) return;
  node.classList.remove("bump");
  void node.offsetWidth;
  node.classList.add("bump");
}

/* ---------- spotlight ---------- */

function spotlightEvaluating(row) {
  const stage = stageOf(row.layer);
  el.spotlight.className = "spotlight is-eval";
  el.spStatus.textContent = "Evaluating";
  el.spIcon.innerHTML = iconFor(row.kind);
  el.spBeat.textContent = row.beat;
  el.spSource.textContent = `Routing through ${STAGE_META[stage].name.toLowerCase()}…`;
  el.spAmount.textContent = row.amount != null ? moneyFlow(row).text : "";
  el.spVerdict.textContent = "…";
  setStageActive(stage);
}

function spotlightVerdict(row, statusLabel) {
  el.spotlight.className = `spotlight is-${row.decision}`;
  el.spStatus.textContent = statusLabel || "Latest decision";
  el.spIcon.innerHTML = iconFor(row.kind);
  el.spBeat.textContent = row.beat;
  el.spSource.textContent = sourceLine(row);
  el.spAmount.textContent = row.amount != null ? moneyFlow(row).text : "";
  el.spVerdict.textContent = row.decision.toUpperCase();
}

/* ---------- decision table ---------- */

function rowTemplate(row) {
  const tr = document.createElement("tr");
  const stage = stageOf(row.layer);
  tr.className = "drow";
  if (row.kind === "self_spend") tr.classList.add("is-self");
  if (isHero(row)) tr.classList.add("is-hero");
  tr.dataset.id = row.id;

  const dotClass =
    row.decision === "approve" ? "dot-approve" : row.decision === "block" ? "dot-block" : "dot-escalate";

  const kindLabel = KIND_LABEL[row.kind] || row.kind;
  const sub = row.job ? `${escapeHtml(row.job)} · ${escapeHtml(kindLabel)}` : escapeHtml(kindLabel);
  const flow = moneyFlow(row);

  tr.innerHTML = `
    <td>
      <div class="cell-kind">
        <span class="kdot ${dotClass}"></span>
        <span class="ktext">
          <span class="kbeat">${escapeHtml(row.beat)}</span>
          <span class="kkind">${sub}${row.kind === "self_spend" ? '<span class="self-tag"> · SPEND</span>' : ""}</span>
        </span>
      </div>
    </td>
    <td><span class="lchip layer-${stage}">${STAGE_META[stage].name}</span></td>
    <td class="cell-risk">${riskChip(row.risk)}</td>
    <td class="cell-amt ${flow.cls}">${flow.text}</td>
    <td><span class="vtag v-${row.decision}">${row.decision.toUpperCase()}</span></td>
    <td class="cell-go">›</td>`;

  tr.addEventListener("click", () => {
    location.hash = `#/event/${encodeURIComponent(row.id)}`;
  });
  return tr;
}

function renderTable(timeline) {
  const rows = [...timeline].reverse(); // newest first
  const key = rows.map((r) => r.id + ":" + r.decision).join("|");
  if (key === lastTableKey) return;
  lastTableKey = key;

  el.decisions.innerHTML = "";
  for (const row of rows) el.decisions.appendChild(rowTemplate(row));
  el.logCount.textContent = `${timeline.length} events`;
}

/* ---------- approval card ---------- */

function renderApproval(state) {
  const pending = state.awaiting_approval;
  pendingApproval = pending || null;
  if (!pending) {
    el.card.classList.add("hidden");
    return;
  }
  el.card.classList.remove("hidden");
  el.cardBeat.textContent = pending.beat || "Owner approval needed";
  el.cardReason.textContent = pending.reason || "";
  const amt = pending.amount != null ? money(pending.amount, pending.currency) : "";
  const risk = pending.risk != null ? `risk ${pending.risk}` : "";
  el.cardRisk.textContent = [amt, risk].filter(Boolean).join(" · ");
}

/* ---------- detail view (presentation-only trace) ---------- */

// Partition the row's refs by which engine layer "owns" them.
function refsByStage(row) {
  const out = { rules: [], model: [], owner: [] };
  for (const r of row.refs || []) {
    if (r === "llm_refine") out.model.push(r);
    else if (r === "phone_escalation") out.owner.push(r);
    else out.rules.push(r);
  }
  return out;
}

// Build the three-layer trace from fields already on the row. No new logic.
function buildTrace(row) {
  const stage = stageOf(row.layer);
  const refs = refsByStage(row);
  const hasModel = (row.refs || []).includes("llm_refine") || stage === "model";
  const hasOwner =
    (row.refs || []).includes("phone_escalation") || stage === "owner" || row.decision === "escalate";

  const steps = [];

  // ---- Step 1: Policy engine (always runs first) ----
  if (stage === "rules") {
    const status = row.decision === "approve" ? "pass" : row.decision === "block" ? "fail" : "warn";
    const label = row.decision === "approve" ? "Passed" : row.decision === "block" ? "Blocked" : "Flagged";
    steps.push({
      status, label, deciding: true,
      title: "Policy engine", tech: "Deterministic rules",
      text: row.reason, refs: refs.rules,
    });
  } else {
    steps.push({
      status: "pass", label: "Cleared", deciding: false,
      title: "Policy engine", tech: "Deterministic rules",
      text: "Deterministic checks passed; routed onward for judgment.", refs: refs.rules,
    });
  }

  // ---- Step 2: NVIDIA Nemotron (bounded judgment) ----
  if (!hasModel) {
    steps.push({
      status: "skip", label: "Not needed", deciding: false,
      title: "NVIDIA Nemotron", tech: "Bounded judgment",
      text: "Policy was decisive. No model judgment required.", refs: [],
    });
  } else if (stage === "model") {
    const status = row.decision === "approve" ? "pass" : row.decision === "block" ? "fail" : "warn";
    const label = row.decision === "approve" ? "Approved" : row.decision === "block" ? "Blocked" : "Flagged";
    steps.push({
      status, label, deciding: true,
      title: "NVIDIA Nemotron", tech: "Bounded judgment",
      text: row.reason, refs: refs.model,
    });
  } else {
    steps.push({
      status: "pass", label: "Refined", deciding: false,
      title: "NVIDIA Nemotron", tech: "Bounded judgment",
      text: "Bounded model refined the rationale within policy limits, but it cannot widen them.",
      refs: refs.model,
    });
  }

  // ---- Step 3: Owner approval (phone tap) ----
  if (!hasOwner) {
    steps.push({
      status: "skip", label: "Not needed", deciding: false,
      title: "Owner approval", tech: "Phone tap",
      text: "Resolved automatically. The owner was not contacted.", refs: [],
    });
  } else {
    const deciding = stage === "owner" || row.decision === "escalate";
    steps.push({
      status: "warn", label: deciding ? "Owner tap" : "Notified", deciding,
      title: "Owner approval", tech: "Phone tap",
      text: deciding
        ? (row.reason || "Pushed to the owner's phone for a one-tap approval.")
        : "Owner was notified for visibility.",
      refs: refs.owner,
    });
  }

  return steps;
}

function refPillsHtml(refs) {
  if (!refs || !refs.length) return "";
  return (
    '<div class="ref-list">' +
    refs.map((r) => `<span class="ref-pill" title="${escapeHtml(r)}">${escapeHtml(refLabel(r))}</span>`).join("") +
    "</div>"
  );
}

function stepHtml(step) {
  const glyph = step.status === "pass" ? "✓" : step.status === "fail" ? "✕" : step.status === "warn" ? "!" : "–";
  return `
    <details class="tstep is-${step.status}"${step.deciding ? " open" : ""}>
      <summary>
        <span class="tdot">${glyph}</span>
        <span class="tstep-title">${escapeHtml(step.title)}</span>
        <span class="tstep-tech">${escapeHtml(step.tech)}</span>
        <span class="tstep-status">${escapeHtml(step.label)}</span>
        <span class="chev">›</span>
      </summary>
      <div class="tstep-body">
        <div>${escapeHtml(step.text)}</div>
        ${refPillsHtml(step.refs)}
      </div>
    </details>`;
}

function renderDetail(id) {
  const row = (currentState.timeline || []).find((r) => r.id === id);
  if (!row) {
    location.hash = "#/overview";
    return false;
  }

  // ----- head -----
  const old = document.querySelector("#view-detail .detail-banner");
  if (old) old.remove();
  el.detailHead.innerHTML = `
    <div class="dh-icon">${iconFor(row.kind)}</div>
    <div class="dh-main">
      <div class="dh-kind">${escapeHtml(KIND_LABEL[row.kind] || row.kind)}</div>
      <h2 class="dh-beat">${escapeHtml(row.beat)}</h2>
    </div>
    <div class="dh-right">
      <span class="dh-amount">${row.amount != null ? money(row.amount, row.currency) : "-"}</span>
      <span class="dh-verdict v-${row.decision}">${row.decision.toUpperCase()}</span>
    </div>`;
  if (isHero(row)) {
    el.detailHead.insertAdjacentHTML(
      "afterend",
      `<div class="detail-banner">⛔ <span><b>Margin-killer refused.</b> This spend would have cost more than the job brings in, so the agent refused it to keep the job profitable.</span></div>`
    );
  }

  // ----- request panel -----
  const sigRefs = row.refs || [];
  el.detailEvent.innerHTML = `
    <div class="panel-head"><h3>Request</h3></div>
    <div class="panel-body">
      <div class="kv">
        <div class="kv-row"><span class="k">Type</span><span class="v">${escapeHtml(KIND_LABEL[row.kind] || row.kind)}</span></div>
        <div class="kv-row"><span class="k">Amount</span><span class="v">${row.amount != null ? money(row.amount, row.currency) : "-"}</span></div>
        <div class="kv-row"><span class="k">Currency</span><span class="v">${escapeHtml(row.currency || "-")}</span></div>
        ${row.category ? `<div class="kv-row"><span class="k">Category</span><span class="v">${escapeHtml(row.category)}</span></div>` : ""}
        <div class="kv-row"><span class="k">Event ID</span><span class="v mono">${escapeHtml(row.id)}</span></div>
        <div class="kv-row"><span class="k">Source</span><span class="v">${escapeHtml(sourceLine(row))}</span></div>
        <div class="kv-row"><span class="k">Signals</span>${sigRefs.length ? refPillsHtml(sigRefs) : '<span class="v">-</span>'}</div>
      </div>
    </div>`;

  // ----- trace panel -----
  const steps = buildTrace(row);
  el.detailTrace.innerHTML = `
    <div class="panel-head"><h3>Decision trace</h3><span class="panel-meta">3-layer engine</span></div>
    <div class="panel-body"><div class="trace">${steps.map(stepHtml).join("")}</div></div>`;

  // ----- outcome panel -----
  const awaiting = currentState.awaiting_approval && currentState.awaiting_approval.event_id === row.id;
  el.detailOutcome.innerHTML = `
    <div class="panel-head"><h3>Outcome</h3></div>
    <div class="panel-body">
      <div class="outcome">
        <span class="verdict-big v-${row.decision}">${verdictGlyph(row.decision)} ${row.decision.toUpperCase()}</span>
        <div class="risk-meter">
          <div class="k"><span>Risk score</span><span>${row.risk}</span></div>
          <div class="risk-bar"><div class="risk-fill" style="width:${Math.round((Number(row.risk) || 0) * 100)}%;background:${verdictColor(row.decision)}"></div></div>
        </div>
        <div class="reason-box">${escapeHtml(row.reason)}</div>
        ${awaiting
          ? `<div class="detail-actions"><button class="btn btn-approve" id="d-approve">Approve</button><button class="btn btn-deny" id="d-deny">Deny</button></div>`
          : ""}
      </div>
    </div>`;

  if (awaiting) {
    const a = currentState.awaiting_approval;
    const da = document.getElementById("d-approve");
    const dd = document.getElementById("d-deny");
    if (da) da.addEventListener("click", () => postDecision(a.approve_url));
    if (dd) dd.addEventListener("click", () => postDecision(a.deny_url));
  }

  setCrumbsDetail(row);
  return true;
}

/* ---------- routing ---------- */

function parseRoute() {
  const h = location.hash || "#/overview";
  if (h.startsWith("#/event/")) {
    return { name: "event", id: decodeURIComponent(h.slice("#/event/".length)) };
  }
  return { name: "overview" };
}

function setCrumbsOverview() {
  el.crumbs.innerHTML = `<span class="crumb cur">Overview</span>`;
}

function setCrumbsDetail(row) {
  el.crumbs.innerHTML =
    `<a class="crumb" href="#/overview">Overview</a>` +
    `<span class="crumb-sep">›</span>` +
    `<span class="crumb cur">${escapeHtml(KIND_LABEL[row.kind] || row.kind)}</span>`;
}

function setNavActive(routeName) {
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  if (routeName === "overview") {
    const o = document.querySelector('.nav-item[data-route="overview"]');
    if (o) o.classList.add("active");
  }
}

function showOverview() {
  el.viewDetail.classList.add("hidden");
  el.viewOverview.classList.remove("hidden");
  setCrumbsOverview();
  setNavActive("overview");
}

function showDetail(id) {
  if (!renderDetail(id)) return;
  el.viewOverview.classList.add("hidden");
  el.viewDetail.classList.remove("hidden");
  setNavActive("detail");
  window.scrollTo({ top: 0, behavior: "auto" });
}

function applyRoute() {
  const r = parseRoute();
  if (r.name === "event") showDetail(r.id);
  else showOverview();
}

window.addEventListener("hashchange", applyRoute);

/* ---------- main render ---------- */

function render(state) {
  currentState = state || { timeline: [] };
  renderMeters(state);
  renderSpark(state);
  renderJobs(state.timeline || []);
  renderCounters(state.timeline || []);
  renderApproval(state);
  renderTable(state.timeline || []);
  const tl = state.timeline || [];
  if (tl.length) spotlightVerdict(tl[tl.length - 1], "Latest decision");
  setStageActive(null);
  applyRoute();
}

/* ---------- idle (nothing analysed yet) ---------- */

function showIdle() {
  currentState = { timeline: [] };
  renderMeters({ seed: SEED_DEFAULT, goal: GOAL_DEFAULT, earnings: 0, spend: 0, net: 0, catch_rate: 0 });
  renderSpark({ seed: SEED_DEFAULT, goal: GOAL_DEFAULT, timeline: [] });
  renderJobs([]);
  renderCounters([]);
  renderApproval({ awaiting_approval: null });
  lastTableKey = "__idle__";
  renderTable([]);
  setStageActive(null);

  el.spotlight.className = "spotlight";
  el.spStatus.textContent = "Idle";
  el.spIcon.innerHTML = iconFor("invoice_payment");
  el.spBeat.textContent = "Nothing running yet";
  el.spSource.textContent = 'Press "Run demo" to watch the back office run.';
  el.spAmount.textContent = "";
  el.spVerdict.textContent = "-";

  applyRoute();
}

/* ---------- data sources ---------- */

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function loadSample() {
  try {
    const state = await fetchJson(SAMPLE_URL);
    render(state);
  } catch (err) {
    el.spBeat.textContent = "Could not load sample_state.json";
    el.spSource.textContent = `Serve this folder over http (see README), then reload. (${err.message})`;
  }
}

async function pollLive() {
  try {
    const state = await fetchJson(LIVE_URL);
    render(state);
  } catch (err) {
    el.sourcePill.textContent = "LIVE (no backend)";
  }
}

/* ---------- live mode ---------- */

function startLive() {
  mode = "live";
  el.sourcePill.textContent = "LIVE";
  el.sourcePill.classList.remove("source-sample");
  el.sourcePill.classList.add("source-live");
  el.toggleLive.textContent = "Go sample";
  // Kick the backend so the live timeline actually plays: rebuild a fresh run,
  // then start it. The agent decides server-side and we mirror it via /state.
  // If the backend isn't reachable, pollLive() surfaces "LIVE (no backend)".
  (async () => {
    try {
      await fetch("/reset", { method: "POST" });
      await fetch("/run", { method: "POST" });
    } catch (_) {
      /* no backend; pollLive reports it */
    }
    pollLive();
  })();
  pollTimer = setInterval(pollLive, POLL_MS);
}

function stopLive() {
  mode = "sample";
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  el.sourcePill.textContent = "SAMPLE DATA";
  el.sourcePill.classList.add("source-sample");
  el.sourcePill.classList.remove("source-live");
  el.toggleLive.textContent = "Go live";
  loadSample();
}

el.toggleLive.addEventListener("click", () => {
  if (mode === "live") stopLive();
  else startLive();
});

/* ---------- approve / deny ---------- */

async function postDecision(url) {
  if (mode === "live" && url) {
    try {
      await fetch(url, { method: "POST" });
    } catch (_) {
      /* backend owns the truth; next poll reconciles */
    }
    pollLive();
    return;
  }
  // sample mode: optimistically clear so the demo flows
  el.card.classList.add("hidden");
}

el.btnApprove.addEventListener("click", () => {
  if (ownerTapResolver) { const r = ownerTapResolver; ownerTapResolver = null; r("approve"); return; }
  postDecision(pendingApproval && pendingApproval.approve_url);
});
el.btnDeny.addEventListener("click", () => {
  if (ownerTapResolver) { const r = ownerTapResolver; ownerTapResolver = null; r("deny"); return; }
  postDecision(pendingApproval && pendingApproval.deny_url);
});

/* ---------- sidebar nav: scroll-to-section ---------- */

document.querySelectorAll(".nav-item[data-scroll]").forEach((a) => {
  a.addEventListener("click", () => {
    const id = a.dataset.scroll;
    if (location.hash && location.hash !== "#/overview") location.hash = "#/overview";
    setTimeout(() => {
      const node = document.getElementById(id);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  });
});

/* ---------- Run demo (sample sequence playback) ---------- */

let demoRunning = false;

function setStatusState(kind, label) {
  el.statusPill.className = `status-pill status-${kind}`;
  el.statusPill.textContent = `● ${label}`;
}

function setStatus(running) {
  setStatusState(running ? "running" : "idle", running ? "Processing" : "Idle");
}

function waitForOwnerTap() {
  return new Promise((resolve) => { ownerTapResolver = resolve; });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function runDemo() {
  if (demoRunning) return;
  if (location.hash && location.hash !== "#/overview") location.hash = "#/overview";
  demoRunning = true;
  el.runDemo.disabled = true;
  setStatus(true);

  if (mode === "live") stopLive();

  let full;
  try {
    full = await fetchJson(SAMPLE_URL);
  } catch (err) {
    demoRunning = false;
    el.runDemo.disabled = false;
    setStatus(false);
    return;
  }

  // Reset to a clean start.
  el.decisions.innerHTML = "";
  el.card.classList.add("hidden");
  el.endCard.classList.add("hidden");
  lastTableKey = "";

  const ordered = [...full.timeline]; // chronological
  const seed = full.seed != null ? Number(full.seed) : SEED_DEFAULT;
  const goal = full.goal != null ? Number(full.goal) : GOAL_DEFAULT;
  let earnings = 0;
  let spend = 0;
  const counts = { approve: 0, block: 0, escalate: 0, rules: 0, model: 0, owner: 0 };
  // Catch-rate climbs only when the agent reinvests in fraud detection.
  let catchRate = Math.max(0, full.catch_rate - 0.28);

  renderMeters({ seed, goal, earnings, spend, catch_rate: catchRate });
  renderSpark({ seed, goal, timeline: [] });
  renderJobs([]);
  const cmapAll = { approve: "cApprove", block: "cBlock", escalate: "cEscalate", rules: "pcRules", model: "pcModel", owner: "pcOwner" };
  for (const k of Object.keys(cmapAll)) el[cmapAll[k]].textContent = "0";

  let pendingState = null;
  try {
    pendingState = await fetchJson(SAMPLE_PENDING_URL);
  } catch (_) {
    pendingState = null;
  }

  for (let i = 0; i < ordered.length; i++) {
    const row = ordered[i];
    const stage = stageOf(row.layer);

    // 1) Show the event entering the engine and light its stage.
    spotlightEvaluating(row);

    if (stage === "owner") {
      // Human-in-the-loop: pause the whole demo until the judge taps.
      const pend = (pendingState && pendingState.awaiting_approval)
        ? pendingState
        : { awaiting_approval: { beat: row.beat, reason: row.reason, risk: row.risk } };
      renderApproval(pend);
      el.card.classList.add("awaiting");
      if (el.cardWait) el.cardWait.classList.add("show");
      setStatusState("waiting", "Waiting on owner");

      const choice = await waitForOwnerTap();

      el.card.classList.remove("awaiting");
      if (el.cardWait) el.cardWait.classList.remove("show");
      setStatus(true);
      if (choice === "deny") {
        row.decision = "block";
        row.reason = "Owner denied the spend from their phone — it left too thin a margin on the job.";
      } else {
        row.decision = "approve";
      }
      await sleep(350);
      el.card.classList.add("hidden");
    } else {
      await sleep(700);
    }

    // 2) Reveal the verdict + move the money.
    let moneyIn = false;
    if (row.decision === "approve" && row.kind === "invoice_payment" && row.amount) {
      earnings += row.amount;
      moneyIn = true;
    }
    if (row.decision === "approve" && row.kind === "self_spend" && row.amount) {
      spend += row.amount;
      if (row.category === "fraud_detection") catchRate = full.catch_rate;
    }

    const played = ordered.slice(0, i + 1);
    const partial = {
      seed, goal, earnings, spend,
      balance: seed + earnings - spend,
      margin: earnings - spend,
      catch_rate: catchRate,
      timeline: played,
    };
    renderMeters(partial);
    renderSpark(partial);
    renderJobs(played);
    if (moneyIn) {
      bumpFlash(el.earnings.closest(".stat"));
      bumpFlash(el.net.closest(".stat"));
      if (el.bhBal) { el.bhBal.classList.remove("grew"); void el.bhBal.offsetWidth; el.bhBal.classList.add("grew"); }
    }

    counts[row.decision] = (counts[row.decision] || 0) + 1;
    counts[stage]++;
    const cmap = { approve: el.cApprove, block: el.cBlock, escalate: el.cEscalate };
    if (cmap[row.decision]) {
      cmap[row.decision].textContent = counts[row.decision];
      bump(cmap[row.decision].closest(".cc"));
    }
    el[STAGE_META[stage].countKey].textContent = counts[stage];

    spotlightVerdict(row, isHero(row) ? "Margin-killer refused" : "Decided");

    // 3) Drop into the live event feed (newest at top).
    el.decisions.insertBefore(rowTemplate(row), el.decisions.firstChild);
    el.logCount.textContent = `${i + 1} events`;

    await sleep(isHero(row) ? 1500 : 650);
    setStageActive(null);
  }

  // Settle to the real totals + show the branded close.
  render(full);
  const t = tallyFrom(full.timeline);
  const finalBalance = full.balance != null ? Number(full.balance) : seed + (Number(full.earnings) - Number(full.spend));
  el.endStat.textContent = `${full.timeline.length} events · ${money(full.earnings)} earned · ${money(full.spend)} spent to deliver · ${t.block} margin-killers refused · balance ${money(finalBalance)}`;
  el.card.classList.add("hidden");
  el.endCard.classList.remove("hidden");

  demoRunning = false;
  el.runDemo.disabled = false;
  setStatusState("complete", `Complete · balance ${money(finalBalance)}`);
}

function bumpFlash(node) {
  if (!node) return;
  node.classList.remove("flash");
  void node.offsetWidth;
  node.classList.add("flash");
}

el.runDemo.addEventListener("click", runDemo);
el.endCardClose.addEventListener("click", () => el.endCard.classList.add("hidden"));

/* ---------- boot ---------- */

showIdle();
