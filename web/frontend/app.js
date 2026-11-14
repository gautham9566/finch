/* Finch Read-Only Monitoring Frontend (Compiled ES2020 from app.ts) */
"use strict";

const PAGE_TITLES = {
  overview: "Overview",
  data: "Data & Quality Gate",
  models: "Models & Calibration",
  rankings: "Rankings Ladder",
  sandbox: "Paper Sandbox & Journal",
  jobs: "Scheduler & Jobs",
  "cloud-resources": "Cloud Resources",
  "cloud-billing": "Cloud Billing",
};

const state = {
  page: "overview",
  sandboxModelFilter: "",
  sandboxChampionOnly: false,
  simDate: "",
  simSlot: "09:20",
  simPresetFilter: "all",
  simModelFilter: "",
  simSymbolFilter: "",
  simSortBy: "pnl_desc",
  lastSimulation: null,
  actionBanner: null,
  trainTargetModel: "all",
  trainStartDate: "",
  trainEndDate: "",
  lastTrainResult: null,
  busyAction: false,
};

function esc(val) {
  const s = val === null || val === undefined ? "" : String(val);
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtInr(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
  return "INR " + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
  return Number(v).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtInt(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
  return Number(v).toLocaleString("en-IN");
}

function fmtIst(iso) {
  if (!iso || typeof iso !== "string") return "--";
  return iso.replace("T", " ").replace("+05:30", " IST").slice(0, 23);
}

function statusPill(label, tone) {
  const text = String(label ?? "--");
  const lower = text.toLowerCase();
  let cls = "pill-neutral";
  if (tone) {
    cls = `pill-${tone}`;
  } else if (["champion", "ready", "healthy", "success", "active", "approve", "approved", "true", "configured"].includes(lower)) {
    cls = "pill-good";
  } else if (["retired", "failed", "unhealthy", "unreachable", "reject", "rejected", "false", "halted"].includes(lower)) {
    cls = "pill-bad";
  } else if (["shadow", "candidate", "contender", "baseline", "not_configured", "pending", "estimate"].some((w) => lower.includes(w))) {
    cls = "pill-warn";
  }
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function emptyBox(message) {
  return `<div class="empty-state">${esc(message)}</div>`;
}

function renderLineChartSvg(points, baseline, color = "#0f4c81", unitPrefix = "INR ") {
  if (!points.length) {
    return emptyBox("No time-series points recorded yet.");
  }
  const w = 680;
  const h = 210;
  const padL = 68;
  const padR = 18;
  const padT = 18;
  const padB = 28;
  const vals = points.map((p) => p.value);
  if (baseline !== undefined) vals.push(baseline);
  let minV = Math.min(...vals);
  let maxV = Math.max(...vals);
  if (Math.abs(maxV - minV) < 1e-6) {
    minV -= 1;
    maxV += 1;
  }
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const xOf = (i) => (points.length === 1 ? padL + innerW / 2 : padL + (i / (points.length - 1)) * innerW);
  const yOf = (v) => padT + innerH - ((v - minV) / (maxV - minV)) * innerH;

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xOf(i).toFixed(1)},${yOf(p.value).toFixed(1)}`)
    .join(" ");

  const gridLines = [0, 0.5, 1]
    .map((t) => {
      const v = minV + t * (maxV - minV);
      const y = yOf(v);
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="currentColor" stroke-opacity="0.12" />
        <text x="${padL - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">${esc(unitPrefix + fmtNum(v, 0))}</text>`;
    })
    .join("");

  const baseLineSvg =
    baseline !== undefined
      ? `<line x1="${padL}" y1="${yOf(baseline).toFixed(1)}" x2="${w - padR}" y2="${yOf(baseline).toFixed(1)}" stroke="#64748b" stroke-dasharray="4 3" stroke-width="1" />`
      : "";

  const dots = points
    .map((p, i) => `<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(p.value).toFixed(1)}" r="2.6" fill="${color}"><title>${esc(p.label)}: ${esc(unitPrefix + fmtNum(p.value, 2))}</title></circle>`)
    .join("");

  const firstLbl = points[0].label;
  const lastLbl = points[points.length - 1].label;

  return `<div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Time series chart">
    ${gridLines}
    ${baseLineSvg}
    <path d="${pathD}" fill="none" stroke="${color}" stroke-width="2" />
    ${dots}
    <text x="${padL}" y="${h - 6}" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">${esc(firstLbl)}</text>
    <text x="${w - padR}" y="${h - 6}" text-anchor="end" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">${esc(lastLbl)}</text>
  </svg></div>`;
}

function renderBarChartSvg(bars, isEstimate = false) {
  if (!bars.length) return emptyBox("No daily series available yet.");
  const w = 680;
  const h = 190;
  const padL = 56;
  const padR = 16;
  const padT = 18;
  const padB = 26;
  const maxV = Math.max(...bars.map((b) => b.value), 1);
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const bw = Math.max(4, innerW / bars.length - 3);
  const fill = isEstimate ? "#ca8a04" : "#0f4c81";

  const rects = bars
    .map((b, i) => {
      const x = padL + (i / bars.length) * innerW + 1;
      const bh = (b.value / maxV) * innerH;
      const y = padT + innerH - bh;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" fill="${fill}" opacity="0.85" rx="1.5"><title>${esc(b.label)}: INR ${fmtNum(b.value, 2)}${isEstimate ? " (estimate)" : ""}</title></rect>`;
    })
    .join("");

  return `<div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Daily bar chart">
    <line x1="${padL}" y1="${padT}" x2="${w - padR}" y2="${padT}" stroke="currentColor" stroke-opacity="0.12" />
    <text x="${padL - 6}" y="${padT + 4}" text-anchor="end" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">INR ${fmtNum(maxV, 0)}</text>
    <line x1="${padL}" y1="${padT + innerH}" x2="${w - padR}" y2="${padT + innerH}" stroke="currentColor" stroke-opacity="0.2" />
    ${rects}
    <text x="${padL}" y="${h - 6}" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">${esc(bars[0].label)}</text>
    <text x="${w - padR}" y="${h - 6}" text-anchor="end" fill="currentColor" fill-opacity="0.65" font-size="10" font-family="monospace">${esc(bars[bars.length - 1].label)}</text>
  </svg></div>`;
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${url}`);
  return res.json();
}

async function postJson(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (!res.ok) {
    const detail = payload?.detail || `HTTP ${res.status} on ${url}`;
    throw new Error(detail);
  }
  return payload;
}

function renderActionBanner() {
  if (!state.actionBanner) return "";
  const { tone, text } = state.actionBanner;
  const cls = tone === "warn" || tone === "bad" ? "banner banner-warn" : "banner";
  return `<div class="${cls}"><span>${esc(text)}</span></div>`;
}

function updateTopBadges(health) {
  const qEl = document.getElementById("status-quality");
  const kEl = document.getElementById("status-kill");
  const uEl = document.getElementById("last-updated");
  if (qEl) {
    const q = health?.latest_quality;
    if (!q) {
      qEl.className = "pill pill-neutral";
      qEl.textContent = "QUALITY: NO REPORT";
    } else if (q.tradeable) {
      qEl.className = "pill pill-good";
      qEl.textContent = `QUALITY: TRADEABLE (${q.trade_date})`;
    } else {
      qEl.className = "pill pill-bad";
      qEl.textContent = `QUALITY: HALTED (${q.trade_date})`;
    }
  }
  if (kEl) {
    const ks = Boolean(health?.kill_switch);
    kEl.className = `pill ${ks ? "pill-bad" : "pill-good"}`;
    kEl.textContent = `KILL SWITCH: ${ks ? "ON" : "OFF"}`;
  }
  if (uEl && health?.checked_at_ist) {
    uEl.textContent = fmtIst(health.checked_at_ist);
  }
}

async function renderOverview() {
  const d = await fetchJson("/api/overview");
  updateTopBadges(d.health);
  const champ = d.champion;
  const kpi = d.sandbox_kpis;
  const pnl = Number(kpi?.cumulative_net_pnl_inr ?? 0);
  const pnlClass = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";

  const curvePts = (d.champion_equity_curve || []).map((r) => ({
    label: String(r.trade_date),
    value: Number(r.equity_inr),
  }));

  const tradesRows = (d.recent_trades || [])
    .map(
      (t) => `<tr>
      <td class="mono">${esc(t.trade_date)}</td>
      <td class="mono"><strong>${esc(t.symbol)}</strong></td>
      <td>${esc(t.model_name)}</td>
      <td class="num">${fmtNum(t.probability, 3)}</td>
      <td>${statusPill(t.risk_decision)}</td>
      <td class="num">${fmtInt(t.quantity)}</td>
      <td class="num">${fmtNum(t.total_cost_inr)}</td>
      <td class="num ${Number(t.net_pnl_inr) > 0 ? "pos" : Number(t.net_pnl_inr) < 0 ? "neg" : ""}">${fmtNum(t.net_pnl_inr)}</td>
    </tr>`
    )
    .join("");

  const qualityRows = (d.recent_quality || [])
    .map(
      (q) => `<tr>
      <td class="mono">${esc(q.trade_date)}</td>
      <td class="num">${fmtInt(q.daily_rows)}</td>
      <td class="num">${fmtInt(q.missing_symbols)}</td>
      <td class="num">${fmtInt(q.bad_ohlc_rows)}</td>
      <td>${statusPill(q.intraday_status)}</td>
      <td>${statusPill(q.tradeable ? "TRADEABLE" : "HALTED", q.tradeable ? "good" : "bad")}</td>
    </tr>`
    )
    .join("");

  return `
    <div class="kpi-grid">
      <div class="card">
        <div class="card-title">Active Champion</div>
        <div class="kpi-val">${esc(champ?.model_name ?? "None yet")}</div>
        <div class="kpi-sub">${champ ? `Test AUC ${fmtNum(champ.test_auc, 3)} · Net ${fmtNum(champ.net_return_pct)}%` : "Run training pipeline to rank models"}</div>
      </div>
      <div class="card">
        <div class="card-title">Paper Equity</div>
        <div class="kpi-val">${fmtInr(kpi?.latest_equity_inr)}</div>
        <div class="kpi-sub">Start ${fmtInr(kpi?.starting_capital_inr)} · ${fmtInt(kpi?.replay_days ?? 0)} days</div>
      </div>
      <div class="card">
        <div class="card-title">Cumulative Net PnL</div>
        <div class="kpi-val ${pnlClass}">${fmtInr(pnl)}</div>
        <div class="kpi-sub">${fmtInt(d.counts?.executed_trades ?? 0)} executed / ${fmtInt(d.counts?.total_journal_rows ?? 0)} signals</div>
      </div>
      <div class="card">
        <div class="card-title">Research Inventory</div>
        <div class="kpi-val">${fmtInt(d.counts?.model_count ?? 0)} models</div>
        <div class="kpi-sub">${fmtInt(d.counts?.sources_count ?? 0)} sources · ${fmtInt(d.counts?.quality_days ?? 0)} quality days</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span>Champion Equity Curve (After Statutory Costs & Slippage)</span>
        <span class="mono muted">${esc(curvePts.length ? curvePts[curvePts.length - 1].label : "No replay data")}</span>
      </div>
      ${renderLineChartSvg(curvePts, Number(kpi?.starting_capital_inr ?? 1000000))}
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-title">Recent Data Quality Gate</div>
        ${
          qualityRows
            ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Rows</th><th class="num">Missing</th><th class="num">Bad OHLC</th><th>Intraday</th><th>Gate</th></tr></thead><tbody>${qualityRows}</tbody></table></div>`
            : emptyBox("No data quality reports stored yet.")
        }
      </div>
      <div class="card">
        <div class="card-title">Latest Sandbox Journal Signals</div>
        ${
          tradesRows
            ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Symbol</th><th>Model</th><th class="num">Prob</th><th>Risk</th><th class="num">Qty</th><th class="num">Cost</th><th class="num">Net PnL</th></tr></thead><tbody>${tradesRows}</tbody></table></div>`
            : emptyBox("No paper-trade signals logged yet.")
        }
      </div>
    </div>
  `;
}

async function renderData() {
  const d = await fetchJson("/api/data");
  const sourcesRows = (d.sources || [])
    .map(
      (s) => `<tr>
      <td><strong>${esc(s.source_name)}</strong></td>
      <td>${statusPill(s.status)}</td>
      <td class="mono">${esc(s.coverage_start ?? "--")} -> ${esc(s.coverage_end ?? "--")}</td>
      <td class="num">${fmtInt(s.row_count)}</td>
      <td class="mono">${fmtIst(s.last_updated_ist)}</td>
      <td class="muted">${esc(s.notes ?? "")}</td>
    </tr>`
    )
    .join("");

  const datasetRows = (d.datasets || [])
    .map(
      (ds) => `<tr>
      <td class="mono"><strong>${esc(ds.version_id)}</strong></td>
      <td class="num">${fmtInt(ds.row_count)}</td>
      <td class="num">${fmtInt(ds.symbol_count)}</td>
      <td class="mono">${esc(ds.min_date)} .. ${esc(ds.max_date)}</td>
      <td class="mono">${fmtIst(ds.created_at_ist)}</td>
    </tr>`
    )
    .join("");

  const featureRows = (d.features || [])
    .map((f, idx) => `<tr><td class="num">${idx + 1}</td><td class="mono">${esc(f.name)}</td><td>${esc(f.description)}</td></tr>`)
    .join("");

  const qRows = (d.quality_reports || [])
    .map(
      (q) => `<tr>
      <td class="mono">${esc(q.trade_date)}</td>
      <td class="num">${fmtInt(q.universe_count)}</td>
      <td class="num">${fmtInt(q.daily_rows)}</td>
      <td class="num">${fmtInt(q.missing_symbols)}</td>
      <td class="num">${fmtInt(q.bad_ohlc_rows)}</td>
      <td class="num">${fmtInt(q.illiquid_dropped)}</td>
      <td>${statusPill(q.global_cues_ok ? "OK" : "MISSING", q.global_cues_ok ? "good" : "bad")}</td>
      <td>${statusPill(q.intraday_status)}</td>
      <td>${statusPill(q.tradeable ? "TRADEABLE" : "HALTED", q.tradeable ? "good" : "bad")}</td>
      <td class="muted">${esc((q.reasons_json || []).join("; ") || "All checks passed")}</td>
    </tr>`
    )
    .join("");

  return `
    <div class="banner">
      Intraday Broker Feed: ${d.intraday_configured ? statusPill("configured", "good") : statusPill("intraday: not configured", "warn")}
      — Point-in-time feature matrix always stamps <span class="mono">known_as_of &lt;= decision_ts</span>.
    </div>

    <div class="card">
      <div class="card-title">Ingestion Sources</div>
      ${
        sourcesRows
          ? `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Status</th><th>Coverage</th><th class="num">Rows</th><th>Updated (IST)</th><th>Notes</th></tr></thead><tbody>${sourcesRows}</tbody></table></div>`
          : emptyBox("No ingestion sources have reported status yet.")
      }
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-title">Immutable Dataset Snapshots</div>
        ${
          datasetRows
            ? `<div class="table-wrap"><table><thead><tr><th>Version</th><th class="num">Rows</th><th class="num">Symbols</th><th>Date Span</th><th>Created (IST)</th></tr></thead><tbody>${datasetRows}</tbody></table></div>`
            : emptyBox("No feature datasets built yet.")
        }
      </div>
      <div class="card">
        <div class="card-title">Point-in-Time Feature Catalog (16 Features)</div>
        <div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Feature</th><th>Definition</th></tr></thead><tbody>${featureRows}</tbody></table></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Daily Data-Quality Gate History</div>
      ${
        qRows
          ? `<div class="table-wrap"><table><thead><tr><th>Trade Date</th><th class="num">Universe</th><th class="num">Rows</th><th class="num">Missing</th><th class="num">Bad OHLC</th><th class="num">Illiquid</th><th>Global Cues</th><th>Intraday</th><th>Gate</th><th>Notes</th></tr></thead><tbody>${qRows}</tbody></table></div>`
          : emptyBox("No quality gate reports generated yet.")
      }
    </div>
  `;
}

function isTrainReady() {
  return Boolean(
    !state.busyAction &&
      state.trainStartDate &&
      state.trainEndDate &&
      state.trainStartDate <= state.trainEndDate
  );
}

function syncTrainButtonState() {
  const btn = document.getElementById("btn-train-models");
  if (btn) btn.disabled = !isTrainReady();
  const hint = document.getElementById("train-date-hint");
  if (hint) {
    if (!state.trainStartDate || !state.trainEndDate) {
      hint.textContent = "Select both Start Date and End Date to enable training.";
    } else if (state.trainStartDate > state.trainEndDate) {
      hint.textContent = "Start Date must be on or before End Date.";
    } else {
      hint.textContent = `Window: ${state.trainStartDate} to ${state.trainEndDate}`;
    }
  }
}

async function renderModels() {
  const d = await fetchJson("/api/models");
  const models = d.models || [];
  const dateBounds = d.date_bounds || {};
  const minDateAttr = dateBounds.min_date ? `min="${esc(dateBounds.min_date)}"` : "";
  const maxDateAttr = dateBounds.max_date ? `max="${esc(dateBounds.max_date)}"` : "";
  const trainable = (d.configured_models || [])
    .map((m) => String(m.model_name ?? m.name ?? ""))
    .filter((n) => n && n !== "regime_classifier");

  const trainRes = state.lastTrainResult;
  const trainResultHtml = trainRes
    ? `<div class="sim-result-box">
        <div class="card-title"><span>Last Training Run (${esc(trainRes.model_name || "all")})</span>${statusPill(trainRes.status)}</div>
        <div class="muted" style="margin-bottom:8px">${esc(trainRes.summary || "")}${trainRes.duration_sec !== undefined ? ` · ${fmtNum(trainRes.duration_sec, 1)}s` : ""}</div>
      </div>`
    : "";

  const rows = models
    .map(
      (m) => `<tr>
      <td><strong>${esc(m.model_name)}</strong><div class="muted">${esc(m.model_type)} · ${esc(m.agent_name)}</div></td>
      <td>${statusPill(m.promotion_status ?? m.status)}</td>
      <td class="mono">${esc(m.dataset_version)}</td>
      <td class="num">${fmtNum(m.train_auc, 3)}</td>
      <td class="num">${fmtNum(m.val_auc, 3)}</td>
      <td class="num"><strong>${fmtNum(m.test_auc, 3)}</strong></td>
      <td class="num">${fmtNum(m.ece, 4)}</td>
      <td class="num ${Number(m.net_return_pct) > 0 ? "pos" : Number(m.net_return_pct) < 0 ? "neg" : ""}">${fmtNum(m.net_return_pct)}%</td>
      <td class="num">${fmtNum(m.max_drawdown_pct)}%</td>
      <td class="num">${fmtNum(Number(m.win_rate ?? 0) * 100, 1)}%</td>
      <td class="num">${fmtNum(m.profit_factor, 2)}</td>
      <td>${m.overfit_flag ? statusPill("OVERFIT", "bad") : m.underfit_flag ? statusPill("UNDERFIT", "warn") : statusPill("CLEAN", "good")}</td>
    </tr>`
    )
    .join("");

  const regimeCards = models
    .slice(0, 4)
    .map((m) => {
      const reg = m.regime_breakdown_json || {};
      const entries = Object.entries(reg);
      return `<div class="card">
        <div class="card-title"><span>${esc(m.model_name)}</span>${statusPill(m.promotion_status ?? m.status)}</div>
        ${
          entries.length
            ? `<table><thead><tr><th>Regime</th><th class="num">Trades</th><th class="num">Win Rate</th><th class="num">Net PnL</th></tr></thead><tbody>${entries
                .map(
                  ([k, v]) =>
                    `<tr><td class="mono">${esc(k)}</td><td class="num">${fmtInt(v?.trades ?? 0)}</td><td class="num">${fmtNum(Number(v?.win_rate ?? 0) * 100, 1)}%</td><td class="num">${fmtNum(v?.net_pnl_inr ?? 0, 2)}</td></tr>`
                )
                .join("")}</tbody></table>`
            : emptyBox("No regime breakdown available.")
        }
      </div>`;
    })
    .join("");

  return `
    ${renderActionBanner()}
    <div class="card control-panel">
      <div class="card-title"><span>Walk-Forward Model Training Controls</span><span class="mono muted">Calibration + MLflow + Scorecard</span></div>
      <div class="filter-bar">
        <label>Target:
          <select id="train-model-select" ${state.busyAction ? "disabled" : ""}>
            <option value="all" ${state.trainTargetModel === "all" ? "selected" : ""}>All Enabled Models (Full Re-rank & Champion Selection)</option>
            ${trainable.map((n) => `<option value="${esc(n)}" ${state.trainTargetModel === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
          </select>
        </label>
        <label>Start Date:
          <input id="train-start-date" type="date" value="${esc(state.trainStartDate)}" ${minDateAttr} ${maxDateAttr} ${state.busyAction ? "disabled" : ""} />
        </label>
        <label>End Date:
          <input id="train-end-date" type="date" value="${esc(state.trainEndDate)}" ${minDateAttr} ${maxDateAttr} ${state.busyAction ? "disabled" : ""} />
        </label>
        <button id="btn-train-models" class="btn btn-primary" ${isTrainReady() ? "" : "disabled"}>
          ${state.busyAction ? "Training Running..." : "Train Model(s) Now"}
        </button>
      </div>
      <div id="train-date-hint" class="muted" style="margin-top:6px">
        ${
          !state.trainStartDate || !state.trainEndDate
            ? "Select both Start Date and End Date to enable training."
            : state.trainStartDate > state.trainEndDate
              ? "Start Date must be on or before End Date."
              : `Window: ${esc(state.trainStartDate)} to ${esc(state.trainEndDate)}`
        }${dateBounds.min_date && dateBounds.max_date ? ` · Available data: ${esc(dateBounds.min_date)} to ${esc(dateBounds.max_date)}` : ""}
      </div>
      ${trainResultHtml}
    </div>

    <div class="card">
      <div class="card-title">Registered Model Cards & Out-of-Sample Walk-Forward Metrics</div>
      ${
        rows
          ? `<div class="table-wrap"><table><thead><tr><th>Model / Agent</th><th>Status</th><th>Dataset</th><th class="num">Train AUC</th><th class="num">Val AUC</th><th class="num">Test AUC</th><th class="num">ECE</th><th class="num">Net Return</th><th class="num">Max DD</th><th class="num">Win Rate</th><th class="num">Profit Factor</th><th>Fit Flag</th></tr></thead><tbody>${rows}</tbody></table></div>`
          : emptyBox("No trained models registered yet. Configured models: " + (d.configured_models || []).length)
      }
    </div>
    <div class="two-col">${regimeCards || ""}</div>
  `;
}

async function renderRankings() {
  const d = await fetchJson("/api/rankings");
  const ladder = d.ladder || [];
  const rows = ladder
    .map(
      (r) => `<tr>
      <td class="num"><strong>#${fmtInt(r.rank)}</strong></td>
      <td><strong>${esc(r.model_name)}</strong></td>
      <td>${esc(r.agent_name ?? "--")}</td>
      <td>${statusPill(r.promotion_status)}</td>
      <td class="num ${Number(r.net_return_pct) > 0 ? "pos" : Number(r.net_return_pct) < 0 ? "neg" : ""}">${fmtNum(r.net_return_pct)}%</td>
      <td class="num">${fmtNum(r.test_auc, 3)}</td>
      <td class="num">${fmtNum(r.train_val_gap, 3)}</td>
      <td class="num">${fmtNum(r.ece, 4)}</td>
      <td class="num">${fmtNum(r.max_drawdown_pct)}%</td>
      <td class="num">${fmtNum(Number(r.win_rate ?? 0) * 100, 1)}%</td>
      <td>${r.beats_baseline ? statusPill("YES", "good") : statusPill("NO", "neutral")}</td>
    </tr>`
    )
    .join("");

  const bars = ladder.map((r) => ({
    label: String(r.model_name),
    value: Math.max(0, Number(r.net_return_pct ?? 0) + 10),
  }));

  const histRows = (d.history || [])
    .slice(-30)
    .reverse()
    .map(
      (h) => `<tr>
      <td class="mono">${fmtIst(h.evaluated_at_ist)}</td>
      <td class="mono">${esc(h.dataset_version)}</td>
      <td class="num">#${fmtInt(h.rank)}</td>
      <td><strong>${esc(h.model_name)}</strong></td>
      <td>${statusPill(h.promotion_status)}</td>
      <td class="num">${fmtNum(h.test_auc, 3)}</td>
      <td class="num">${fmtNum(h.net_return_pct)}%</td>
    </tr>`
    )
    .join("");

  return `
    <div class="card">
      <div class="card-title">Model Promotion Ladder (Champion / Shadow / Candidate / Retired)</div>
      ${
        rows
          ? `<div class="table-wrap"><table><thead><tr><th class="num">Rank</th><th>Model</th><th>Agent</th><th>Role</th><th class="num">Net Return</th><th class="num">Test AUC</th><th class="num">Train-Val Gap</th><th class="num">ECE</th><th class="num">Max DD</th><th class="num">Win Rate</th><th>Beats Baseline</th></tr></thead><tbody>${rows}</tbody></table></div>`
          : emptyBox("No model scorecard rankings computed yet.")
      }
    </div>
    <div class="two-col">
      <div class="card">
        <div class="card-title">Shifted Score Bar (+10% baseline offset)</div>
        ${bars.length ? renderBarChartSvg(bars, false) : emptyBox("No ranked scores yet.")}
      </div>
      <div class="card">
        <div class="card-title">Evaluation Rank History</div>
        ${
          histRows
            ? `<div class="table-wrap"><table><thead><tr><th>Evaluated</th><th>Dataset</th><th class="num">Rank</th><th>Model</th><th>Status</th><th class="num">Test AUC</th><th class="num">Net %</th></tr></thead><tbody>${histRows}</tbody></table></div>`
            : emptyBox("No historical ranking snapshots yet.")
        }
      </div>
    </div>
  `;
}

function simEffectivePnl(s) {
  if (s.effective_pnl_inr !== undefined && s.effective_pnl_inr !== null) {
    return Number(s.effective_pnl_inr);
  }
  return Number(s.quantity) > 0
    ? Number(s.net_pnl_inr ?? 0)
    : Number(s.counterfactual_pnl_inr ?? 0);
}

function filterAndSortSimSignals(rawSignals) {
  const symQuery = (state.simSymbolFilter || "").trim().toUpperCase();
  let rows = (rawSignals || []).filter((s) => {
    if (state.simModelFilter && s.model_name !== state.simModelFilter) return false;
    if (symQuery && !String(s.symbol || "").toUpperCase().includes(symQuery)) return false;
    const pnl = simEffectivePnl(s);
    if (state.simPresetFilter === "profitable_only" && pnl <= 0) return false;
    if (state.simPresetFilter === "losses_only" && pnl >= 0) return false;
    if (state.simPresetFilter === "top_profits" && pnl <= 0) return false;
    if (
      state.simPresetFilter === "top_achieving" &&
      Number(s.probability ?? 0) < 0.6 &&
      s.exit_reason !== "TARGET_HIT" &&
      Number(s.actual_target_hit ?? 0) !== 1
    ) {
      return false;
    }
    return true;
  });

  const sorters = {
    pnl_desc: (a, b) => simEffectivePnl(b) - simEffectivePnl(a) || Number(b.probability) - Number(a.probability),
    pnl_asc: (a, b) => simEffectivePnl(a) - simEffectivePnl(b) || Number(b.probability) - Number(a.probability),
    prob_desc: (a, b) => Number(b.probability) - Number(a.probability),
    actual_ret_desc: (a, b) => Number(b.actual_return_pct ?? -999) - Number(a.actual_return_pct ?? -999),
    symbol_asc: (a, b) => String(a.symbol || "").localeCompare(String(b.symbol || "")) || String(a.model_name || "").localeCompare(String(b.model_name || "")),
    model_asc: (a, b) => String(a.model_name || "").localeCompare(String(b.model_name || "")) || Number(b.probability) - Number(a.probability),
  };
  const sortKey = state.simPresetFilter === "top_profits" && state.simSortBy === "pnl_desc" ? "pnl_desc" : state.simSortBy;
  rows = rows.slice().sort(sorters[sortKey] || sorters.pnl_desc);
  if (state.simPresetFilter === "top_profits") rows = rows.slice(0, 15);
  return rows;
}

function renderSimTableHtml(sim) {
  const rows = filterAndSortSimSignals(sim?.signals || []);
  if (!rows.length) {
    return emptyBox("No generated signals match the current filters.");
  }
  const showActuals =
    Boolean(sim?.is_past_date) ||
    (sim?.signals || []).some((s) => s.actual_close !== null && s.actual_close !== undefined);

  const body = rows
    .map((s) => {
      const pnl = simEffectivePnl(s);
      const isCf = Number(s.quantity) === 0;
      const predRet =
        s.predicted_return_pct !== undefined && s.predicted_return_pct !== null
          ? `${Number(s.predicted_return_pct) >= 0 ? "+" : ""}${fmtNum(s.predicted_return_pct)}%`
          : "--";
      const actualRet =
        s.actual_return_pct !== undefined && s.actual_return_pct !== null
          ? `${Number(s.actual_return_pct) >= 0 ? "+" : ""}${fmtNum(s.actual_return_pct)}%`
          : "--";
      const actualCells = showActuals
        ? `<td class="num">${fmtNum(s.actual_open)} / ${fmtNum(s.actual_close)}<div class="muted">H ${fmtNum(s.actual_high)} · L ${fmtNum(s.actual_low)}</div></td>
           <td class="num ${Number(s.actual_return_pct) > 0 ? "pos" : Number(s.actual_return_pct) < 0 ? "neg" : ""}">${actualRet}</td>`
        : "";
      return `<tr>
        <td class="mono"><strong>${esc(s.symbol)}</strong></td>
        <td>${esc(s.model_name)}${s.is_champion_trade ? ' <span class="pill pill-good">CHAMP</span>' : ""}</td>
        <td><span class="mono">${esc(s.direction)}</span></td>
        <td class="num">${fmtNum(s.probability, 3)}</td>
        <td class="num">${fmtNum(s.entry_price)} / ${fmtNum(s.target_price)}<div class="muted">Stop ${fmtNum(s.stop_price)} · Pred ${predRet}</div></td>
        ${actualCells}
        <td>${statusPill(s.risk_decision)}</td>
        <td class="num">${fmtInt(s.quantity)}</td>
        <td class="num">${fmtNum(s.fill_price)} / ${fmtNum(s.exit_price)}</td>
        <td class="mono">${esc(s.exit_reason ?? "--")}</td>
        <td class="num ${pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}">${fmtNum(pnl)}${isCf ? ' <span class="muted">(CF)</span>' : ""}</td>
        <td class="muted">${esc((s.decision_reason || "").slice(0, 60))}</td>
      </tr>`;
    })
    .join("");

  const actualHeaders = showActuals
    ? `<th class="num">Actual O / C</th><th class="num">Actual Ret</th>`
    : "";

  return `<div class="muted" style="margin-bottom:6px">Showing ${fmtInt(rows.length)} of ${fmtInt((sim?.signals || []).length)} signals</div>
    <div class="table-wrap"><table><thead><tr>
      <th>Symbol</th><th>Model</th><th>Dir</th><th class="num">Prob</th><th class="num">Pred Entry / Target</th>${actualHeaders}<th>Risk</th><th class="num">Qty</th><th class="num">Fill / Exit</th><th>Reason</th><th class="num">PnL (INR)</th><th>Decision Detail</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

async function renderSandbox() {
  const qs = new URLSearchParams({ limit: "150" });
  if (state.sandboxModelFilter) qs.set("model_name", state.sandboxModelFilter);
  if (state.sandboxChampionOnly) qs.set("only_champion", "true");
  const d = await fetchJson(`/api/sandbox?${qs.toString()}`);

  const totals = d.model_totals || [];
  const modelsList = Array.from(new Set(totals.map((t) => String(t.model_name))));

  const activeModel = state.sandboxModelFilter || (totals[0]?.model_name ?? "");
  const series = (d.daily_summaries || [])
    .filter((r) => !activeModel || r.model_name === activeModel)
    .map((r) => ({ label: String(r.trade_date), value: Number(r.equity_inr) }));

  const totalsRows = totals
    .map(
      (m) => `<tr>
      <td><strong>${esc(m.model_name)}</strong></td>
      <td class="num">${fmtInt(m.days)}</td>
      <td class="num">${fmtInt(m.total_candidates)}</td>
      <td class="num">${fmtInt(m.total_executed)}</td>
      <td class="num">${fmtInt(m.total_rejected)}</td>
      <td class="num">${fmtNum(m.total_gross_pnl_inr)}</td>
      <td class="num">${fmtNum(m.total_cost_inr)}</td>
      <td class="num">${fmtNum(m.total_slippage_inr)}</td>
      <td class="num ${Number(m.total_net_pnl_inr) > 0 ? "pos" : Number(m.total_net_pnl_inr) < 0 ? "neg" : ""}">${fmtNum(m.total_net_pnl_inr)}</td>
      <td class="num">${fmtInr(m.latest_equity_inr)}</td>
    </tr>`
    )
    .join("");

  const jRows = (d.journal || [])
    .map(
      (j) => `<tr>
      <td class="mono">${esc(j.trade_date)}</td>
      <td class="mono"><strong>${esc(j.symbol)}</strong></td>
      <td>${esc(j.model_name)}${j.is_champion_trade ? ' <span class="pill pill-good">CHAMP</span>' : ""}</td>
      <td class="num">${fmtNum(j.probability, 3)}</td>
      <td>${statusPill(j.coordinator_decision)}</td>
      <td>${statusPill(j.llm_decision)}</td>
      <td>${statusPill(j.risk_decision)}</td>
      <td class="num">${fmtInt(j.quantity)}</td>
      <td class="num">${fmtNum(j.fill_price)}</td>
      <td class="num">${fmtNum(j.exit_price)}</td>
      <td><span class="mono">${esc(j.exit_reason ?? "--")}</span></td>
      <td class="num">${fmtNum(j.total_cost_inr)}</td>
      <td class="num ${Number(j.net_pnl_inr) > 0 ? "pos" : Number(j.net_pnl_inr) < 0 ? "neg" : ""}">${fmtNum(j.net_pnl_inr)}</td>
      <td class="num muted">${j.counterfactual_pnl_inr !== null ? fmtNum(j.counterfactual_pnl_inr) : "--"}</td>
    </tr>`
    )
    .join("");

  const risk = d.risk_config || {};
  const availableDates = d.available_dates || [];
  const availableModels = d.available_models || [];
  const latestDateLabel = availableDates[0] || "auto";

  const sim = state.lastSimulation;
  const simModels = sim
    ? Array.from(new Set((sim.signals || []).map((s) => s.model_name).filter(Boolean))).sort()
    : [];

  const simBoxHtml = sim
    ? `<div class="sim-result-box">
        <div class="card-title">
          <span>Generated Signals: ${esc(sim.trade_date || "--")} (${esc(sim.slot || "09:20")} IST) · Champion ${esc(sim.champion || "--")}${sim.is_past_date ? ' <span class="pill pill-warn">HISTORICAL · PREDICTED VS ACTUAL</span>' : ""}</span>
          <span>Executed ${fmtInt(sim.executed_champion_trades ?? 0)} · Net ${fmtInr(sim.champion_net_pnl_inr ?? 0)}</span>
        </div>
        <div class="filter-bar" style="margin-bottom:10px">
          <label>View / Preset:
            <select id="sim-preset-filter">
              <option value="all" ${state.simPresetFilter === "all" ? "selected" : ""}>All Signals</option>
              <option value="top_achieving" ${state.simPresetFilter === "top_achieving" ? "selected" : ""}>Top Achieving (Prob >= 60% or Target Hit)</option>
              <option value="top_profits" ${state.simPresetFilter === "top_profits" ? "selected" : ""}>Top Profits (PnL > 0, Top 15)</option>
              <option value="profitable_only" ${state.simPresetFilter === "profitable_only" ? "selected" : ""}>Profitable Only (PnL > 0)</option>
              <option value="losses_only" ${state.simPresetFilter === "losses_only" ? "selected" : ""}>Losses Only (PnL < 0)</option>
            </select>
          </label>
          <label>Model:
            <select id="sim-model-filter">
              <option value="" ${state.simModelFilter === "" ? "selected" : ""}>All Models (${simModels.length})</option>
              ${simModels.map((m) => `<option value="${esc(m)}" ${state.simModelFilter === m ? "selected" : ""}>${esc(m)}</option>`).join("")}
            </select>
          </label>
          <label>Stock:
            <input id="sim-symbol-filter" type="text" placeholder="Search symbol..." value="${esc(state.simSymbolFilter)}" />
          </label>
          <label>Sort By:
            <select id="sim-sort-by">
              <option value="pnl_desc" ${state.simSortBy === "pnl_desc" ? "selected" : ""}>Overall Profit/Loss (High -> Low)</option>
              <option value="pnl_asc" ${state.simSortBy === "pnl_asc" ? "selected" : ""}>Overall Profit/Loss (Low -> High)</option>
              <option value="prob_desc" ${state.simSortBy === "prob_desc" ? "selected" : ""}>Predicted Probability (High -> Low)</option>
              <option value="actual_ret_desc" ${state.simSortBy === "actual_ret_desc" ? "selected" : ""}>Actual Return % (High -> Low)</option>
              <option value="symbol_asc" ${state.simSortBy === "symbol_asc" ? "selected" : ""}>Stock Symbol (A -> Z)</option>
              <option value="model_asc" ${state.simSortBy === "model_asc" ? "selected" : ""}>Model Name (A -> Z)</option>
            </select>
          </label>
        </div>
        <div id="sim-signals-table-wrap">${renderSimTableHtml(sim)}</div>
      </div>`
    : "";

  return `
    ${renderActionBanner()}
    <div class="card control-panel">
      <div class="card-title"><span>Live / Selected-Date Signal & Paper-Trade Simulator</span><span class="mono muted">Uses current Paper Trading Options</span></div>
      <div class="form-grid">
        <label class="form-field">Trading Date (Select Date to Enable Generate)
          <select id="sim-date-select" ${state.busyAction ? "disabled" : ""}>
            <option value="" ${state.simDate === "" ? "selected" : ""}>-- Select a trading date --</option>
            <option value="latest" ${state.simDate === "latest" ? "selected" : ""}>Live / Latest Available (${esc(latestDateLabel)})</option>
            ${availableDates.map((dt) => `<option value="${esc(dt)}" ${state.simDate === dt ? "selected" : ""}>${esc(dt)}</option>`).join("")}
          </select>
        </label>
        <label class="form-field">Snapshot Slot (IST)
          <select id="sim-slot-select" ${state.busyAction ? "disabled" : ""}>
            <option value="09:20" ${state.simSlot === "09:20" ? "selected" : ""}>09:20 IST (Opening + 5m Bar)</option>
            <option value="08:45" ${state.simSlot === "08:45" ? "selected" : ""}>08:45 IST (Pre-Open Global Cues)</option>
          </select>
        </label>
      </div>
      <div class="action-row">
        <button id="btn-simulate-date" class="btn btn-primary" ${state.busyAction || !state.simDate ? "disabled" : ""}>Generate Signals & Paper Trades</button>
        <button id="btn-replay-5d" class="btn" ${state.busyAction ? "disabled" : ""}>Replay Last 5 Days</button>
        <button id="btn-replay-25d" class="btn" ${state.busyAction ? "disabled" : ""}>Replay Full 25 Days</button>
        <button id="btn-sb-train" class="btn btn-warn" ${state.busyAction ? "disabled" : ""}>Train All Models</button>
        <button id="btn-reset-trades" class="btn btn-danger" ${state.busyAction ? "disabled" : ""}>Reset All Paper Trades</button>
      </div>
      ${simBoxHtml}
    </div>

    <div class="card">
      <div class="card-title"><span>Paper Trading Options (Risk & Execution Config)</span><span class="mono muted">Persisted in Postgres system_state</span></div>
      <div class="form-grid">
        <label class="form-field">Starting Capital (INR)
          <input id="opt-capital" type="number" min="10000" step="10000" value="${esc(risk.capital_inr ?? 1000000)}" />
        </label>
        <label class="form-field">Risk / Trade (%)
          <input id="opt-risk-pct" type="number" min="0.05" max="5" step="0.05" value="${esc((Number(risk.risk_per_trade_pct ?? 0.005) * 100).toFixed(2))}" />
        </label>
        <label class="form-field">Max Trades / Day
          <input id="opt-max-trades" type="number" min="1" max="50" step="1" value="${esc(risk.max_trades_per_day ?? 6)}" />
        </label>
        <label class="form-field">Min Probability (0.50–0.90)
          <input id="opt-min-prob" type="number" min="0.50" max="0.90" step="0.01" value="${esc(Number(risk.min_probability ?? 0.52).toFixed(2))}" />
        </label>
        <label class="form-field">Max Position Cap (%)
          <input id="opt-max-pos-pct" type="number" min="1" max="50" step="1" value="${esc((Number(risk.max_position_pct ?? 0.10) * 100).toFixed(1))}" />
        </label>
        <label class="form-field">Max Opening Gap (%)
          <input id="opt-max-gap-pct" type="number" min="0.5" max="15" step="0.5" value="${esc((Number(risk.max_opening_gap_pct ?? 0.035) * 100).toFixed(1))}" />
        </label>
        <label class="form-field">Direction Filter
          <select id="opt-direction">
            <option value="BOTH" ${String(risk.direction_filter || "BOTH") === "BOTH" ? "selected" : ""}>BOTH (Long & Short)</option>
            <option value="LONG" ${String(risk.direction_filter || "") === "LONG" ? "selected" : ""}>LONG Only</option>
            <option value="SHORT" ${String(risk.direction_filter || "") === "SHORT" ? "selected" : ""}>SHORT Only</option>
          </select>
        </label>
        <label class="form-field">Active Model Override
          <select id="opt-champion">
            <option value="" ${!risk.preferred_champion ? "selected" : ""}>Auto (Top Ranked Champion)</option>
            ${availableModels.map((m) => `<option value="${esc(m)}" ${risk.preferred_champion === m ? "selected" : ""}>${esc(m)}</option>`).join("")}
          </select>
        </label>
        <label class="form-field">Kill Switch
          <select id="opt-kill-switch">
            <option value="false" ${!risk.kill_switch ? "selected" : ""}>OFF (Paper Trading Enabled)</option>
            <option value="true" ${risk.kill_switch ? "selected" : ""}>ON (Halt All Paper Trades)</option>
          </select>
        </label>
      </div>
      <div class="action-row">
        <button id="btn-save-options" class="btn btn-primary" ${state.busyAction ? "disabled" : ""}>Save Paper Trading Options</button>
        <button id="btn-reset-options" class="btn" ${state.busyAction ? "disabled" : ""}>Reset Defaults</button>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="card"><div class="card-title">Paper Capital</div><div class="kpi-val">${fmtInr(risk.capital_inr)}</div><div class="kpi-sub">Max daily loss ${fmtNum(Number(risk.max_daily_loss_pct ?? 0) * 100, 1)}%</div></div>
      <div class="card"><div class="card-title">Position & Sector Caps</div><div class="kpi-val">${fmtNum(Number(risk.max_position_pct ?? 0) * 100, 0)}% / ${fmtNum(Number(risk.max_sector_pct ?? 0) * 100, 0)}%</div><div class="kpi-sub">Risk/trade ${fmtNum(Number(risk.risk_per_trade_pct ?? 0) * 100, 2)}% · Min prob ${fmtNum(risk.min_probability, 2)}</div></div>
      <div class="card"><div class="card-title">Session Windows (IST)</div><div class="kpi-val">${esc(risk.entry_cutoff_ist)} / ${esc(risk.square_off_ist)}</div><div class="kpi-sub">Max ${fmtInt(risk.max_trades_per_day)} trades/day · ${esc(risk.direction_filter || "BOTH")}</div></div>
      <div class="card"><div class="card-title">LLM Reviewer Stub</div><div class="kpi-val">APPROVE</div><div class="kpi-sub">${esc(risk.preferred_champion ? "Override: " + risk.preferred_champion : "Champion: Auto")}</div></div>
    </div>

    <div class="card">
      <div class="card-title"><span>Sandbox Equity Curve (${esc(activeModel || "No active series")})</span></div>
      ${renderLineChartSvg(series, Number(risk.capital_inr ?? 1000000))}
    </div>

    <div class="card">
      <div class="card-title">Per-Model Paper Portfolio Summary</div>
      ${
        totalsRows
          ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th class="num">Days</th><th class="num">Candidates</th><th class="num">Executed</th><th class="num">Rejected</th><th class="num">Gross PnL</th><th class="num">Costs</th><th class="num">Slippage</th><th class="num">Net PnL</th><th class="num">Latest Equity</th></tr></thead><tbody>${totalsRows}</tbody></table></div>`
          : emptyBox("No sandbox replay summaries recorded yet.")
      }
    </div>

    <div class="card">
      <div class="card-title">Full Trade & Counterfactual Decision Journal</div>
      <div class="filter-bar">
        <label>Model:
          <select id="sb-model-filter">
            <option value="">All models</option>
            ${modelsList.map((m) => `<option value="${esc(m)}" ${m === state.sandboxModelFilter ? "selected" : ""}>${esc(m)}</option>`).join("")}
          </select>
        </label>
        <label><input type="checkbox" id="sb-champ-only" ${state.sandboxChampionOnly ? "checked" : ""} /> Champion trades only</label>
      </div>
      ${
        jRows
          ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Symbol</th><th>Model</th><th class="num">Prob</th><th>Coord</th><th>LLM</th><th>Risk</th><th class="num">Qty</th><th class="num">Fill</th><th class="num">Exit</th><th>Reason</th><th class="num">Cost</th><th class="num">Net PnL</th><th class="num">Counterfactual</th></tr></thead><tbody>${jRows}</tbody></table></div>`
          : emptyBox("No matching journal records found.")
      }
    </div>
  `;
}

async function renderJobs() {
  const d = await fetchJson("/api/jobs");
  const schedRows = (d.schedules || [])
    .map(
      (j) => `<tr>
      <td class="mono"><strong>${esc(j.name)}</strong></td>
      <td class="mono">${esc(j.cron)}</td>
      <td>${esc(j.description)}</td>
      <td class="mono muted">${esc(j.command)}</td>
    </tr>`
    )
    .join("");

  const runRows = (d.runs || [])
    .map(
      (r) => `<tr>
      <td class="num">${fmtInt(r.id)}</td>
      <td class="mono"><strong>${esc(r.job_name)}</strong></td>
      <td>${statusPill(r.status)}</td>
      <td class="mono">${fmtIst(r.started_at_ist)}</td>
      <td class="num">${r.duration_sec !== null ? fmtNum(r.duration_sec, 1) + "s" : "--"}</td>
      <td class="muted">${esc((r.summary || "").slice(0, 140))}</td>
    </tr>`
    )
    .join("");

  return `
    <div class="card">
      <div class="card-title"><span>Registered IST Cron Schedules</span><span class="mono muted">${esc(d.timezone)}</span></div>
      <div class="table-wrap"><table><thead><tr><th>Job Name</th><th>Cron (IST)</th><th>Description</th><th>Command</th></tr></thead><tbody>${schedRows}</tbody></table></div>
    </div>

    <div class="card">
      <div class="card-title">Recent Execution History</div>
      ${
        runRows
          ? `<div class="table-wrap"><table><thead><tr><th class="num">ID</th><th>Job</th><th>Status</th><th>Started (IST)</th><th class="num">Duration</th><th>Summary</th></tr></thead><tbody>${runRows}</tbody></table></div>`
          : emptyBox("No job runs logged yet in Postgres job_runs.")
      }
    </div>
  `;
}

async function renderCloudResources() {
  const d = await fetchJson("/api/cloud/resources");
  const gcp = d.gcp || {};
  const vm = d.vm_host || {};
  const mon = d.cloud_monitoring || {};
  const b = d.bucket || {};
  const loc = d.local_storage || {};

  const cpuPts = (mon.series || []).map((p) => ({
    label: String(p.ts).slice(11, 16),
    value: Number(p.cpu_pct),
  }));

  const svcRows = (d.services || [])
    .map(
      (s) => `<tr>
      <td class="mono"><strong>${esc(s.service)}</strong></td>
      <td>${statusPill(s.status)}</td>
      <td class="muted">${esc(s.detail)}</td>
    </tr>`
    )
    .join("");

  return `
    <div class="kpi-grid">
      <div class="card"><div class="card-title">GCP VM Instance</div><div class="kpi-val">${esc(gcp.vm_name)}</div><div class="kpi-sub">${esc(gcp.project_id)} · ${esc(gcp.vm_zone)}</div></div>
      <div class="card"><div class="card-title">VM CPU / Load</div><div class="kpi-val">${mon.latest_cpu_pct !== null ? fmtNum(mon.latest_cpu_pct) + "%" : "Load " + (vm.load_1m ?? "--")}</div><div class="kpi-sub">${mon.available ? "Cloud Monitoring live" : "Host load average fallback"}</div></div>
      <div class="card"><div class="card-title">RAM & Disk Usage</div><div class="kpi-val">${vm.mem_used_pct !== null ? fmtNum(vm.mem_used_pct, 1) + "%" : "--"} / ${vm.disk_used_pct !== null ? fmtNum(vm.disk_used_pct, 1) + "%" : "--"}</div><div class="kpi-sub">${fmtNum(vm.disk_used_gb, 1)} / ${fmtNum(vm.disk_total_gb, 1)} GB disk</div></div>
      <div class="card"><div class="card-title">GCS Lake & Postgres</div><div class="kpi-val">${fmtInt(b.object_count)} objects</div><div class="kpi-sub">GCS ${fmtNum(b.total_mb, 2)} MB · PG ${loc.postgres_mb !== null ? fmtNum(loc.postgres_mb, 2) + " MB" : "--"} · Parquet ${fmtNum(loc.parquet_mb, 2)} MB</div></div>
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-title">Container & Subsystem Health</div>
        <div class="table-wrap"><table><thead><tr><th>Service</th><th>Status</th><th>Detail</th></tr></thead><tbody>${svcRows}</tbody></table></div>
      </div>
      <div class="card">
        <div class="card-title"><span>VM CPU Utilization (Cloud Monitoring, Last 1h)</span><span class="pill pill-neutral">${esc(mon.source)}</span></div>
        ${cpuPts.length ? renderLineChartSvg(cpuPts, undefined, "#0f4c81", "% ") : emptyBox("Cloud Monitoring samples not available yet.")}
      </div>
    </div>
  `;
}

async function renderCloudBilling() {
  const d = await fetchJson("/api/cloud/billing");
  const isEst = Boolean(d.is_estimate);
  const budgetPct = d.budget_inr > 0 ? (Number(d.mtd_total) / Number(d.budget_inr)) * 100 : 0;

  const svcRows = (d.service_breakdown || [])
    .map(
      (s) => `<tr>
      <td>${esc(s.service)} ${s.is_estimate ? '<span class="pill pill-warn">ESTIMATE</span>' : ""}</td>
      <td class="num">${fmtInr(s.amount)}</td>
      <td class="num">${s.monthly_estimate !== undefined ? fmtInr(s.monthly_estimate) : "--"}</td>
    </tr>`
    )
    .join("");

  const bars = (d.daily_series || []).map((p) => ({
    label: String(p.date),
    value: Number(p.amount),
  }));

  return `
    <div class="banner ${isEst ? "banner-warn" : ""}">
      <strong>Billing Feed Status:</strong> ${statusPill(d.export_status, isEst ? "warn" : "good")}
      ${d.setup_note ? `<div class="muted" style="margin-top:4px">${esc(d.setup_note)}</div>` : ""}
    </div>

    <div class="kpi-grid">
      <div class="card">
        <div class="card-title">Month-to-Date Spend ${isEst ? '<span class="pill pill-warn">ESTIMATE</span>' : ""}</div>
        <div class="kpi-val">${fmtInr(d.mtd_total)}</div>
        <div class="kpi-sub">Source: ${esc(d.source)}</div>
      </div>
      <div class="card">
        <div class="card-title">Projected Full-Month ${isEst ? '<span class="pill pill-warn">ESTIMATE</span>' : ""}</div>
        <div class="kpi-val">${fmtInr(d.projected_month_total)}</div>
        <div class="kpi-sub">Based on current month run-rate</div>
      </div>
      <div class="card">
        <div class="card-title">Configured GCP Budget</div>
        <div class="kpi-val">${fmtInr(d.budget_inr)}</div>
        <div class="kpi-sub">Alert thresholds: ${(d.budget_thresholds || []).map((x) => `${Math.round(x * 100)}%`).join(" / ")}</div>
      </div>
      <div class="card">
        <div class="card-title">Budget Utilization ${isEst ? '<span class="pill pill-warn">ESTIMATE</span>' : ""}</div>
        <div class="kpi-val">${fmtNum(budgetPct, 1)}%</div>
        <div class="kpi-sub">Region: asia-south1 (Mumbai)</div>
      </div>
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-title"><span>Daily Cost Breakdown</span>${isEst ? '<span class="pill pill-warn">ESTIMATE</span>' : '<span class="pill pill-good">BIGQUERY</span>'}</div>
        ${renderBarChartSvg(bars, isEst)}
      </div>
      <div class="card">
        <div class="card-title">Service Line Items</div>
        <div class="table-wrap"><table><thead><tr><th>Service</th><th class="num">MTD Spend</th><th class="num">Est. Monthly</th></tr></thead><tbody>${svcRows}</tbody></table></div>
      </div>
    </div>
  `;
}

async function loadPage(page) {
  state.page = page;
  const titleEl = document.getElementById("page-title");
  if (titleEl) titleEl.textContent = PAGE_TITLES[page];

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });

  const content = document.getElementById("content");
  if (!content) return;
  content.innerHTML = `<div class="loading-box">Loading ${esc(PAGE_TITLES[page])}...</div>`;

  try {
    let html = "";
    switch (page) {
      case "overview":
        html = await renderOverview();
        break;
      case "data":
        html = await renderData();
        break;
      case "models":
        html = await renderModels();
        break;
      case "rankings":
        html = await renderRankings();
        break;
      case "sandbox":
        html = await renderSandbox();
        break;
      case "jobs":
        html = await renderJobs();
        break;
      case "cloud-resources":
        html = await renderCloudResources();
        break;
      case "cloud-billing":
        html = await renderCloudBilling();
        break;
    }
    content.innerHTML = html;
    void fetchJson("/api/health").then(updateTopBadges).catch(() => {});

    if (page === "models") {
      document.getElementById("train-model-select")?.addEventListener("change", (ev) => {
        state.trainTargetModel = ev.target.value;
      });
      document.getElementById("train-start-date")?.addEventListener("change", (ev) => {
        state.trainStartDate = ev.target.value;
        syncTrainButtonState();
      });
      document.getElementById("train-end-date")?.addEventListener("change", (ev) => {
        state.trainEndDate = ev.target.value;
        syncTrainButtonState();
      });
      document.getElementById("btn-train-models")?.addEventListener("click", async () => {
        if (!isTrainReady()) return;
        state.busyAction = true;
        state.actionBanner = {
          tone: "warn",
          text: `Running walk-forward training for '${state.trainTargetModel}' (${state.trainStartDate} to ${state.trainEndDate})...`,
        };
        void loadPage("models");
        try {
          const res = await postJson("/api/actions/train", {
            model_name: state.trainTargetModel,
            start_date: state.trainStartDate,
            end_date: state.trainEndDate,
          });
          state.lastTrainResult = res;
          state.actionBanner = { tone: "good", text: res.summary || `Completed training for ${state.trainTargetModel}.` };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Training failed: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("models");
        }
      });
    }

    if (page === "sandbox") {
      document.getElementById("sim-date-select")?.addEventListener("change", (ev) => {
        state.simDate = ev.target.value;
        const simBtn = document.getElementById("btn-simulate-date");
        if (simBtn) simBtn.disabled = state.busyAction || !state.simDate;
      });
      document.getElementById("sim-slot-select")?.addEventListener("change", (ev) => {
        state.simSlot = ev.target.value;
      });

      const refreshSimTableOnly = () => {
        const wrap = document.getElementById("sim-signals-table-wrap");
        if (wrap && state.lastSimulation) {
          wrap.innerHTML = renderSimTableHtml(state.lastSimulation);
        }
      };
      document.getElementById("sim-preset-filter")?.addEventListener("change", (ev) => {
        state.simPresetFilter = ev.target.value;
        refreshSimTableOnly();
      });
      document.getElementById("sim-model-filter")?.addEventListener("change", (ev) => {
        state.simModelFilter = ev.target.value;
        refreshSimTableOnly();
      });
      document.getElementById("sim-symbol-filter")?.addEventListener("input", (ev) => {
        state.simSymbolFilter = ev.target.value;
        refreshSimTableOnly();
      });
      document.getElementById("sim-sort-by")?.addEventListener("change", (ev) => {
        state.simSortBy = ev.target.value;
        refreshSimTableOnly();
      });

      document.getElementById("btn-save-options")?.addEventListener("click", async () => {
        const body = {
          capital_inr: Number(document.getElementById("opt-capital")?.value ?? 1000000),
          risk_per_trade_pct: Number(document.getElementById("opt-risk-pct")?.value ?? 0.5) / 100,
          max_trades_per_day: Number(document.getElementById("opt-max-trades")?.value ?? 6),
          min_probability: Number(document.getElementById("opt-min-prob")?.value ?? 0.52),
          max_position_pct: Number(document.getElementById("opt-max-pos-pct")?.value ?? 10) / 100,
          max_opening_gap_pct: Number(document.getElementById("opt-max-gap-pct")?.value ?? 3.5) / 100,
          direction_filter: document.getElementById("opt-direction")?.value ?? "BOTH",
          preferred_champion: document.getElementById("opt-champion")?.value ?? "",
          kill_switch: document.getElementById("opt-kill-switch")?.value === "true",
        };
        state.busyAction = true;
        try {
          const res = await postJson("/api/actions/paper-options", body);
          state.actionBanner = { tone: "good", text: `Saved paper trading options: ${res.summary || "updated"}` };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Failed to save options: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      });

      document.getElementById("btn-reset-options")?.addEventListener("click", async () => {
        state.busyAction = true;
        try {
          await postJson("/api/actions/paper-options", { reset_defaults: true });
          state.actionBanner = { tone: "good", text: "Paper trading options reset to config/risk.yaml defaults." };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Failed to reset options: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      });

      document.getElementById("btn-simulate-date")?.addEventListener("click", async () => {
        state.busyAction = true;
        state.actionBanner = { tone: "warn", text: `Simulating live/selected date (${state.simDate}, slot ${state.simSlot})...` };
        void loadPage("sandbox");
        try {
          const res = await postJson("/api/actions/simulate", {
            mode: state.simDate === "latest" ? "live" : "single_date",
            trade_date: state.simDate,
            slot: state.simSlot,
          });
          state.lastSimulation = res;
          state.actionBanner = {
            tone: "good",
            text: `Simulated ${res.trade_date} (${res.slot} IST) with ${res.champion}: ${res.executed_champion_trades} executed trades, Net ${fmtInr(res.champion_net_pnl_inr)}.`,
          };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Simulation failed: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      });

      const runMultiReplay = async (days) => {
        state.busyAction = true;
        state.actionBanner = { tone: "warn", text: `Replaying last ${days} trading sessions with active paper-trading options...` };
        void loadPage("sandbox");
        try {
          const res = await postJson("/api/actions/simulate", {
            mode: "replay_range",
            days,
            slot: state.simSlot,
          });
          state.lastSimulation = res;
          state.actionBanner = {
            tone: "good",
            text: `Replayed ${res.days_simulated} days (${res.start_date} to ${res.end_date}): ${res.executed_champion_trades} champion trades, Net ${fmtInr(res.champion_net_pnl_inr)}.`,
          };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Replay failed: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      };

      document.getElementById("btn-replay-5d")?.addEventListener("click", () => void runMultiReplay(5));
      document.getElementById("btn-replay-25d")?.addEventListener("click", () => void runMultiReplay(25));

      document.getElementById("btn-sb-train")?.addEventListener("click", async () => {
        state.busyAction = true;
        state.actionBanner = { tone: "warn", text: "Training all models (walk-forward + calibration + scorecard)..." };
        void loadPage("sandbox");
        try {
          const res = await postJson("/api/actions/train", { model_name: "all" });
          state.lastTrainResult = res;
          state.actionBanner = { tone: "good", text: res.summary || "Model training completed." };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Training failed: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      });

      document.getElementById("btn-reset-trades")?.addEventListener("click", async () => {
        state.busyAction = true;
        try {
          const res = await postJson("/api/actions/reset-trades", {});
          state.lastSimulation = null;
          state.actionBanner = {
            tone: "good",
            text: `Reset complete: cleared ${fmtInt(res.deleted_journal_rows)} journal rows & ${fmtInt(res.deleted_summary_rows)} daily summaries (capital ${fmtInr(res.capital_inr)}).`,
          };
        } catch (err) {
          state.actionBanner = { tone: "bad", text: `Reset failed: ${err.message}` };
        } finally {
          state.busyAction = false;
          void loadPage("sandbox");
        }
      });

      document.getElementById("sb-model-filter")?.addEventListener("change", (ev) => {
        state.sandboxModelFilter = ev.target.value;
        void loadPage("sandbox");
      });
      document.getElementById("sb-champ-only")?.addEventListener("change", (ev) => {
        state.sandboxChampionOnly = ev.target.checked;
        void loadPage("sandbox");
      });
    }
  } catch (err) {
    content.innerHTML = `<div class="banner banner-warn">Failed to load ${esc(PAGE_TITLES[page])}: ${esc(err.message)}</div>`;
  }
}

function initTheme() {
  const saved = localStorage.getItem("finch_theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
  const btn = document.getElementById("theme-btn");
  if (btn) btn.textContent = saved === "dark" ? "Light" : "Dark";
  btn?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("finch_theme", next);
    btn.textContent = next === "dark" ? "Light" : "Dark";
  });
}

window.addEventListener("DOMContentLoaded", () => {
  initTheme();
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.page;
      if (target) void loadPage(target);
    });
  });
  document.getElementById("refresh-btn")?.addEventListener("click", () => {
    void loadPage(state.page);
  });
  void loadPage("overview");
});
