const state = {
  catalog: null,
  profiles: new Map(),
  runs: [],
  activeRunId: null,
  loadedResultRunId: null,
  pollTimer: null,
  estimateTimer: null,
  chart: null,
};

const $ = (id) => document.getElementById(id);
const gridInputs = () => [...document.querySelectorAll("[data-grid-key]")];

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败: ${response.status}`);
  return payload;
}

function profileValue(profile, key, fallback = "") {
  return profile?.values?.[key] ?? fallback;
}

function localInputValue(value) {
  if (!value) return "";
  return String(value).replace(" ", "T").slice(0, 16);
}

function populateProfile(name) {
  const profile = state.profiles.get(name);
  if (!profile) return;
  $("experiment-name").value ||= `${profileValue(profile, "symbol", "策略")} 三年参数研究`;
  $("provider-select").value = profileValue(profile, "provider", "bitget");
  $("symbol-input").value = profileValue(profile, "symbol", "BTCUSDT");
  $("duration-select").value = profileValue(profile, "duration", 300);
  $("start-time-input").value = localInputValue(profileValue(profile, "start_time"));
  $("end-time-input").value = localInputValue(profileValue(profile, "end_time"));
  $("strategy-select").value = profileValue(profile, "strategy", "live_decision");
  $("initial-equity-input").value = profileValue(profile, "initial_equity", 20000);
  $("fee-rate-input").value = profileValue(profile, "fee_rate", 0.00023);
  $("slippage-rate-input").value = profileValue(profile, "slippage_rate", 0);
  $("cache-enabled-input").checked = String(profileValue(profile, "cache_enabled", "true")).toLowerCase() !== "false";
  const defaults = {
    "indicator.merged_dkx_hull_ut.hull_length": 55,
    "indicator.merged_dkx_hull_ut.hull_length_mult": 1,
    "indicator.merged_dkx_hull_ut.hull_variation": "Hma",
    "indicator.merged_dkx_hull_ut.ut_atr_period": 6,
    "indicator.merged_dkx_hull_ut.ut_sensitivity": 2,
    "indicator.merged_dkx_hull_ut.ut_use_heikin_ashi": "false",
    "indicator.stc.length": 80,
    "indicator.stc.fast_length": 27,
    "indicator.stc.slow_length": 50,
    "indicator.stc.factor": 0.5,
  };
  gridInputs().forEach((input) => {
    const key = input.dataset.gridKey;
    const profileKey = key.startsWith("indicator.") ? null : key;
    input.value = profileKey ? profileValue(profile, profileKey, "") : defaults[key] ?? "";
  });
  scheduleEstimate();
}

function parseGridValue(input) {
  return input.value.split(",").map((item) => item.trim()).filter(Boolean);
}

function buildPayload() {
  const grid = {};
  gridInputs().forEach((input) => {
    const values = parseGridValue(input);
    if (values.length) grid[input.dataset.gridKey] = values;
  });
  return {
    name: $("experiment-name").value.trim(),
    profile: $("profile-select").value,
    overrides: {
      provider: $("provider-select").value,
      symbol: $("symbol-input").value.trim().toUpperCase(),
      duration_seconds: Number($("duration-select").value),
      strategy: $("strategy-select").value,
      start_time: $("start-time-input").value.replace("T", " "),
      end_time: $("end-time-input").value.replace("T", " "),
      initial_equity: Number($("initial-equity-input").value),
      fee_rate: Number($("fee-rate-input").value),
      slippage_rate: Number($("slippage-rate-input").value),
      cache_enabled: $("cache-enabled-input").checked,
    },
    grid,
  };
}

function combinationCount() {
  return gridInputs().reduce((count, input) => count * Math.max(parseGridValue(input).length, 1), 1);
}

function scheduleEstimate() {
  clearTimeout(state.estimateTimer);
  const count = combinationCount();
  $("combination-estimate").textContent = `组合数：${count} · ${count === 1 ? "生成完整报告和图表" : "矩阵仅保存轻量摘要，候选组合可一键复测"}`;
  state.estimateTimer = setTimeout(refreshEstimate, 350);
}

async function refreshEstimate() {
  try {
    const payload = await fetchJson("/api/backtests/estimate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildPayload()),
    });
    const cache = payload.cache;
    const barsLabel = payload.estimated_bars == null ? "滚动数据窗口" : `<strong>${formatNumber(payload.estimated_bars, 0)}</strong> 根目标 K 线`;
    const cacheStatus = cache.complete ? '<span class="ok">缓存完整</span>' : cache.exists ? '<span class="warn">缓存不完整，将自动补齐</span>' : '<span class="warn">没有本地缓存，首次运行需要下载</span>';
    $("data-estimate").innerHTML = [
      `${barsLabel} · <strong>${payload.combinations}</strong> 个组合`,
      `${cacheStatus} · ${escapeHtml(cache.first_time || "--")} 至 ${escapeHtml(cache.last_time || "--")} · ${formatBytes(cache.size_bytes)}`,
      escapeHtml(payload.execution_class),
    ].join("<br>");
    $("form-error").textContent = "";
  } catch (error) {
    $("data-estimate").textContent = error.message;
  }
}

async function submitRun() {
  const button = $("run-button");
  button.disabled = true;
  $("form-error").textContent = "";
  try {
    const status = await fetchJson("/api/backtests/runs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildPayload()),
    });
    state.activeRunId = status.run_id;
    state.loadedResultRunId = null;
    await refreshRuns();
  } catch (error) {
    $("form-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshRuns() {
  try {
    const payload = await fetchJson("/api/backtests/runs?limit=30");
    state.runs = payload.runs;
    renderRuns();
    const active = state.activeRunId && state.runs.find((item) => item.run_id === state.activeRunId);
    if (active?.status === "succeeded" && state.loadedResultRunId !== active.run_id) await loadResult(active.run_id);
  } catch (error) {
    $("run-list").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderRuns() {
  if (!state.runs.length) {
    $("run-list").innerHTML = '<div class="empty-state">还没有 UI 回测实验</div>';
    return;
  }
  $("run-list").innerHTML = state.runs.map((run) => `
    <button class="run-item ${run.run_id === state.activeRunId ? "is-active" : ""}" data-run-id="${escapeHtml(run.run_id)}">
      <div class="run-title"><span>${escapeHtml(run.name || run.run_id)}</span><span class="status-dot status-${run.status}">${statusLabel(run.status)}</span></div>
      <div class="run-meta"><span>${escapeHtml(run.phase || "--")}</span><span>${run.completed_combinations || 0}/${run.total_combinations || 1} 组 · ${escapeHtml(run.created_at || "")}</span></div>
      <div class="progress-track"><i style="width:${Number(run.progress || 0)}%"></i></div>
    </button>`).join("");
  document.querySelectorAll(".run-item").forEach((item) => item.addEventListener("click", () => selectRun(item.dataset.runId)));
}

async function selectRun(runId) {
  state.activeRunId = runId;
  renderRuns();
  const run = state.runs.find((item) => item.run_id === runId);
  if (run?.status === "succeeded") await loadResult(runId);
  else if (run?.error) $("form-error").textContent = run.error;
}

async function loadResult(runId) {
  const result = await fetchJson(`/api/backtests/runs/${encodeURIComponent(runId)}/result`);
  renderResult(result);
  state.loadedResultRunId = runId;
  $("result-section").classList.remove("is-hidden");
  if (result.type === "single") {
    try {
      const chart = await fetchJson(`/api/backtests/runs/${encodeURIComponent(runId)}/chart`);
      renderChart(chart);
    } catch (_) { $("chart-section").classList.add("is-hidden"); }
  } else {
    $("chart-section").classList.add("is-hidden");
  }
  $("result-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderResult(result) {
  const best = result.best || {};
  $("result-title").textContent = result.name || "实验结果";
  const badge = $("result-verdict");
  badge.textContent = best.verdict || "--";
  badge.className = `verdict-badge ${best.verdict === "谨慎" ? "caution" : best.verdict === "不通过" ? "fail" : ""}`;
  $("score-explanation").textContent = result.score_explanation || "";
  const metrics = [
    ["稳健分", formatNumber(best.robust_score)], ["总收益", formatPct(best.return_pct)],
    ["最大回撤", formatPct(best.max_drawdown_pct)], ["盈利因子", formatNumber(best.profit_factor)],
    ["净利润", formatNumber(best.net_profit)], ["胜率", formatPct(best.win_rate_pct)],
    ["交易数", formatNumber(best.trade_count, 0)], ["手续费", formatNumber(best.total_fees)],
    ["最差年度", formatPct(best.worst_year_return_pct)], ["正收益年度", formatPct(Number(best.positive_year_ratio || 0) * 100)],
  ];
  $("metric-grid").innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("");
  const splits = [
    ["研究段 60%", best.research_return_pct], ["验证段 20%", best.validation_return_pct], ["测试段 20%", best.test_return_pct],
    ...Object.entries(best.yearly_returns_pct || {}).map(([year, value]) => [`${year} 年`, value]),
  ];
  $("split-grid").innerHTML = splits.map(([label, value]) => `<div class="split-item">${label}<strong>${formatPct(value)}</strong></div>`).join("");
  renderTable(result.rows || []);
  renderHeatmap(result);
}

function renderTable(rows) {
  const table = $("result-table");
  table.innerHTML = `<thead><tr><th>排名</th><th>结论</th><th>稳健分</th><th>总收益</th><th>验证段</th><th>测试段</th><th>最大回撤</th><th>盈利因子</th><th>交易数</th><th>参数</th><th></th></tr></thead><tbody>${rows.slice(0, 100).map((row, index) => `
    <tr><td>#${row.rank}</td><td>${escapeHtml(row.verdict)}</td><td>${formatNumber(row.robust_score)}</td><td>${formatPct(row.return_pct)}</td><td>${formatPct(row.validation_return_pct)}</td><td>${formatPct(row.test_return_pct)}</td><td>${formatPct(row.max_drawdown_pct)}</td><td>${formatNumber(row.profit_factor)}</td><td>${formatNumber(row.trade_count,0)}</td><td title="${escapeHtml(JSON.stringify(row.parameters))}">${escapeHtml(compactParameters(row.parameters))}</td><td><button class="mini-button" data-row-index="${index}">复测</button></td></tr>`).join("")}</tbody>`;
  table.querySelectorAll("[data-row-index]").forEach((button) => button.addEventListener("click", () => refillCandidate(rows[Number(button.dataset.rowIndex)])));
}

function renderHeatmap(result) {
  const keys = Object.keys(result.grid || {}).filter((key) => (result.grid[key] || []).length > 1);
  const section = $("heatmap-section");
  if (keys.length < 2) { section.classList.add("is-hidden"); return; }
  section.classList.remove("is-hidden");
  const [xKey, yKey] = keys;
  const xs = result.grid[xKey];
  const ys = result.grid[yKey];
  const scores = (result.rows || []).map((row) => Number(row.robust_score));
  const min = Math.min(...scores), max = Math.max(...scores);
  const cells = [`<div class="heat-axis">${escapeHtml(shortKey(yKey))} ↓ / ${escapeHtml(shortKey(xKey))} →</div>`, ...xs.map((x) => `<div class="heat-axis">${escapeHtml(x)}</div>` )];
  ys.forEach((y) => {
    cells.push(`<div class="heat-axis">${escapeHtml(y)}</div>`);
    xs.forEach((x) => {
      const matching = (result.rows || []).filter((row) => String(row.parameters[xKey]) === String(x) && String(row.parameters[yKey]) === String(y));
      const score = matching.length ? Math.max(...matching.map((row) => Number(row.robust_score))) : min;
      const ratio = max === min ? .5 : (score - min) / (max - min);
      const hue = 8 + ratio * 142;
      cells.push(`<div class="heat-cell" style="background:hsl(${hue} 52% ${24 + ratio * 18}%)">${formatNumber(score,1)}</div>`);
    });
  });
  const map = $("heatmap");
  map.style.gridTemplateColumns = `repeat(${xs.length + 1}, minmax(92px, 1fr))`;
  map.innerHTML = cells.join("");
}

async function refillCandidate(row) {
  try {
    const source = await fetchJson(`/api/backtests/runs/${encodeURIComponent(state.activeRunId)}/request`);
    if (source.profile && state.profiles.has(source.profile)) {
      $("profile-select").value = source.profile;
      populateProfile(source.profile);
    }
    const overrides = source.overrides || {};
    if (overrides.provider) $("provider-select").value = overrides.provider;
    if (overrides.symbol) $("symbol-input").value = overrides.symbol;
    if (overrides.duration_seconds) $("duration-select").value = overrides.duration_seconds;
    if (overrides.strategy) $("strategy-select").value = overrides.strategy;
    if (overrides.start_time) $("start-time-input").value = localInputValue(overrides.start_time);
    if (overrides.end_time) $("end-time-input").value = localInputValue(overrides.end_time);
  } catch (_) {
    // The selected parameters are still useful if the historical request cannot be read.
  }
  Object.entries(row.parameters || {}).forEach(([key, value]) => {
    const input = document.querySelector(`[data-grid-key="${CSS.escape(key)}"]`);
    if (input) input.value = value;
  });
  $("experiment-name").value = `${$("experiment-name").value.replace(/ · 复测$/, "")} · 复测`;
  scheduleEstimate();
  document.querySelector(".builder-card").scrollTo({ top: 0, behavior: "smooth" });
}

function renderChart(payload) {
  $("chart-section").classList.remove("is-hidden");
  $("chart-note").textContent = `${formatNumber(payload.source_bar_count,0)} 根压缩为 ${formatNumber(payload.preview_bar_count,0)} 根预览`;
  const container = $("backtest-chart");
  container.innerHTML = "";
  if (state.chart) state.chart.remove();
  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 500, layout: { background: { color: "#091512" }, textColor: "#8ea69d" },
    grid: { vertLines: { color: "rgba(181,218,204,.06)" }, horzLines: { color: "rgba(181,218,204,.06)" } },
    timeScale: { borderColor: "rgba(181,218,204,.12)", timeVisible: true }, rightPriceScale: { borderColor: "rgba(181,218,204,.12)" },
  });
  const candles = state.chart.addCandlestickSeries({ upColor: "#5ee0a0", downColor: "#ff7777", borderVisible: false, wickUpColor: "#5ee0a0", wickDownColor: "#ff7777" });
  candles.setData(payload.candles || []);
  candles.setMarkers((payload.markers || []).sort((a,b) => a.time - b.time));
  state.chart.timeScale().fitContent();
  new ResizeObserver(() => state.chart?.applyOptions({ width: container.clientWidth })).observe(container);
}

function compactParameters(parameters = {}) {
  return Object.entries(parameters).map(([key, value]) => `${shortKey(key)}=${value}`).join(" · ") || "基准参数";
}
function shortKey(key) { return key.split(".").pop().replaceAll("_points", "").replaceAll("_", " "); }
function statusLabel(status) { return ({queued:"排队中",running:"运行中",succeeded:"已完成",failed:"失败",interrupted:"已中断"})[status] || status; }
function formatNumber(value, digits = 2) { if (value == null || value === "") return "--"; const number = Number(value); return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "--"; }
function formatPct(value) { const formatted = formatNumber(value); return formatted === "--" ? "--" : `${formatted}%`; }
function formatBytes(value) { const bytes = Number(value || 0); if (!bytes) return "0 MB"; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]); }

async function boot() {
  state.catalog = await fetchJson("/api/backtests/catalog");
  state.catalog.profiles.forEach((profile) => state.profiles.set(profile.name, profile));
  $("profile-select").innerHTML = state.catalog.profiles.map((profile) => `<option value="${escapeHtml(profile.name)}">${escapeHtml(profile.name)}</option>`).join("");
  $("duration-select").innerHTML = state.catalog.durations.map((duration) => `<option value="${duration}">${duration >= 3600 ? `${duration/3600}h` : `${duration/60}m`}</option>`).join("");
  const strategies = state.catalog.strategies.flatMap((item) => [item.name, ...(item.aliases || [])]);
  $("strategy-select").innerHTML = strategies.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  $("profile-select").addEventListener("change", () => populateProfile($("profile-select").value));
  ["provider-select","symbol-input","duration-select","start-time-input","end-time-input","strategy-select","initial-equity-input","fee-rate-input","slippage-rate-input","cache-enabled-input"].forEach((id) => $(id).addEventListener("change", scheduleEstimate));
  gridInputs().forEach((input) => input.addEventListener("input", scheduleEstimate));
  $("run-button").addEventListener("click", submitRun);
  $("refresh-runs-button").addEventListener("click", refreshRuns);
  const recommended = state.profiles.has("btc_5m_range_cached") ? "btc_5m_range_cached" : state.catalog.profiles[0]?.name || "";
  $("profile-select").value = recommended;
  populateProfile(recommended);
  await refreshRuns();
  state.pollTimer = setInterval(refreshRuns, 2500);
}

boot().catch((error) => { $("form-error").textContent = error.message; });
