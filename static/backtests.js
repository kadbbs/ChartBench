const state = {
  catalog: null,
  profiles: new Map(),
  runs: [],
  activeRunId: null,
  loadedResultRunId: null,
  pollTimer: null,
  estimateTimer: null,
  chart: null,
  activeResult: null,
  workflow: "signal_path",
  strategyDetailsReturnFocus: null,
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
  $("experiment-name").value ||= state.workflow === "signal_path"
    ? `${profileValue(profile, "symbol", "策略")} 无风控信号路径`
    : `${profileValue(profile, "symbol", "策略")} 三年参数研究`;
  $("provider-select").value = profileValue(profile, "provider", "bitget");
  $("symbol-input").value = profileValue(profile, "symbol", "BTCUSDT");
  $("duration-select").value = profileValue(profile, "duration", 300);
  $("start-time-input").value = localInputValue(profileValue(profile, "start_time"));
  $("end-time-input").value = localInputValue(profileValue(profile, "end_time"));
  $("strategy-select").value = profileValue(profile, "strategy", "live_decision");
  updateStrategyHint();
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
  const values = [];
  input.value.split(",").map((item) => item.trim()).filter(Boolean).forEach((token) => {
    const range = expandNumericRange(token);
    if (range) values.push(...range);
    else values.push(token);
  });
  return [...new Set(values)];
}

function expandNumericRange(token) {
  const match = token.match(/^(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const precision = Math.max(...match.slice(1).map((item) => (item.split(".")[1] || "").length));
  const scale = 10 ** precision;
  const start = Math.round(Number(match[1]) * scale);
  const end = Math.round(Number(match[2]) * scale);
  const step = Math.round(Number(match[3]) * scale);
  if (!step || (end - start) * step < 0) return null;
  const values = [];
  const within = step > 0 ? (value) => value <= end : (value) => value >= end;
  for (let value = start; within(value) && values.length <= 256; value += step) {
    values.push(String(value / scale));
  }
  return values;
}

function buildPayload() {
  const overrides = {
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
  };
  if (state.workflow === "signal_path") {
    return {
      workflow: "signal_path",
      action: $("path-action-select").value,
      name: $("experiment-name").value.trim(),
      profile: $("profile-select").value,
      overrides,
      context_bars: Number($("path-context-bars-input").value),
      baseline_run_id: $("path-baseline-run-select").value || null,
      stop_unit: $("path-stop-unit-select").value,
      stop_values: $("path-stop-values-input").value.trim(),
      take_values: $("path-take-values-input").value.trim(),
      max_reentries: Number($("path-max-reentries-input").value),
      reentry_cooldown_bars: Number($("path-reentry-cooldown-input").value),
      intrabar_policy: $("path-intrabar-policy-select").value,
      reveal_test: $("path-reveal-test-input").checked,
    };
  }
  const grid = {};
  gridInputs().forEach((input) => {
    const values = parseGridValue(input);
    if (values.length) grid[input.dataset.gridKey] = values;
  });
  return {
    name: $("experiment-name").value.trim(),
    profile: $("profile-select").value,
    overrides,
    grid,
  };
}

function combinationCount() {
  if (state.workflow === "signal_path") {
    if ($("path-action-select").value === "baseline") return 1;
    return Math.max(parseAxisInput($("path-stop-values-input").value).length, 1)
      * Math.max(parseAxisInput($("path-take-values-input").value).length, 1);
  }
  return gridInputs().reduce((count, input) => count * Math.max(parseGridValue(input).length, 1), 1);
}

function parseAxisInput(value) {
  const values = [];
  String(value || "").split(",").map((item) => item.trim()).filter(Boolean).forEach((token) => {
    const range = expandNumericRange(token);
    if (range) values.push(...range);
    else if (Number.isFinite(Number(token))) values.push(String(Number(token)));
  });
  return [...new Set(values)];
}

function scheduleEstimate() {
  clearTimeout(state.estimateTimer);
  const count = combinationCount();
  const baseline = state.workflow === "signal_path" && $("path-action-select").value === "baseline";
  $("combination-estimate").innerHTML = `
    <span>本次实验</span>
    <strong>${baseline ? "无风控基准样本" : `${formatNumber(count, 0)} 个组合`}</strong>
    <small>${baseline ? "导出 K 线、节点和大模型 JSONL" : count === 1 ? "生成完整报告和图表" : "同一份信号路径快速重放"}</small>
  `;
  $("run-button").querySelector("span").textContent = baseline ? "生成基准样本" : state.workflow === "signal_path" ? "开始路径矩阵" : "开始回测实验";
  state.estimateTimer = setTimeout(refreshEstimate, 350);
}

function applyWorkflow() {
  state.workflow = $("workflow-select").value;
  const pathMode = state.workflow === "signal_path";
  $("path-action-field").classList.toggle("is-hidden", !pathMode);
  $("path-parameters-section").classList.toggle("is-hidden", !pathMode);
  $("standard-parameters-section").classList.toggle("is-hidden", pathMode);
  const matrixMode = pathMode && $("path-action-select").value === "matrix";
  document.querySelectorAll(".matrix-only-field").forEach((item) => item.classList.toggle("is-disabled", !matrixMode));
  document.querySelectorAll(".matrix-only-field input, .matrix-only-field select").forEach((item) => { item.disabled = !matrixMode; });
  if (pathMode && state.profiles.has("btc_5m_signal_path") && $("profile-select").value !== "btc_5m_signal_path") {
    $("profile-select").value = "btc_5m_signal_path";
    $("experiment-name").value = "";
    populateProfile("btc_5m_signal_path");
  }
  refreshBaselineOptions();
  scheduleEstimate();
}

function updateStrategyHint() {
  const selectedName = $("strategy-select").value;
  const strategy = strategyCatalogItem(effectiveStrategyName(selectedName));
  const hint = $("strategy-hint");
  const isReentry = Boolean(strategy?.details?.reentry_confirmation_duration_seconds);
  hint.classList.toggle("is-reentry", isReentry);
  hint.textContent = strategy?.details?.summary || "该策略尚未提供摘要，可打开策略说明查看可用信息。";
}

function strategyCatalogItem(name) {
  return state.catalog?.strategies?.find((item) => item.name === name || (item.aliases || []).includes(name)) || null;
}

function effectiveStrategyName(selectedName) {
  if (selectedName !== "live_decision") return selectedName;
  const profile = state.profiles.get($("profile-select").value);
  return profileValue(profile, "signal_strategy", "marker_signal");
}

function strategyDurationLabel(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "未启用";
  if (value % 86400 === 0) return `${value / 86400}D`;
  if (value % 3600 === 0) return `${value / 3600}H`;
  if (value % 60 === 0) return `${value / 60}m`;
  return `${value}s`;
}

function renderStrategyDetails() {
  const selectedName = $("strategy-select").value;
  const effectiveName = effectiveStrategyName(selectedName);
  const strategy = strategyCatalogItem(effectiveName) || strategyCatalogItem(selectedName);
  if (!strategy) {
    $("strategy-details-title").textContent = "策略说明不可用";
    $("strategy-details-name").textContent = selectedName || "--";
    $("strategy-details-content").innerHTML = '<p class="strategy-summary">目录中没有找到该策略的说明。</p>';
    return;
  }
  const details = strategy.details || {};
  const profile = state.profiles.get($("profile-select").value);
  const htfEnabled = String(profileValue(profile, "htf_hull_filter_enabled", "true")).toLowerCase() !== "false";
  const primarySeconds = details.primary_htf_duration_seconds
    || (htfEnabled ? Number(profileValue(profile, "htf_hull_duration_seconds", 0)) : 0);
  const reentrySeconds = details.reentry_confirmation_duration_seconds;
  const refreshStartupOnSameSide = Boolean(details.refresh_startup_on_same_side_signal);
  const aliasNote = selectedName === "live_decision"
    ? `Profile 委托：live_decision → ${effectiveName}`
    : selectedName !== strategy.name ? `当前使用别名：${selectedName} → ${strategy.name}` : `主策略：${strategy.name}`;
  const tags = (details.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  const sections = (details.sections || []).map((section) => `
    <section class="strategy-rule-section">
      <h3>${escapeHtml(section.title || "规则")}</h3>
      <ul>${(section.items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </section>
  `).join("");

  $("strategy-details-title").textContent = details.title || strategy.name;
  $("strategy-details-name").textContent = aliasNote;
  $("strategy-details-content").innerHTML = `
    <p class="strategy-summary">${escapeHtml(details.summary || "暂无策略摘要。")}</p>
    <div class="strategy-tags">${tags}</div>
    <div class="strategy-context-grid">
      <div><span>执行周期</span><strong>${escapeHtml(strategyDurationLabel($("duration-select").value))}</strong></div>
      <div><span>主趋势过滤</span><strong>${escapeHtml(htfEnabled ? strategyDurationLabel(primarySeconds) : "已关闭")}</strong></div>
      <div><span>重复开仓确认</span><strong>${escapeHtml(reentrySeconds ? strategyDurationLabel(reentrySeconds) : "不允许")}</strong></div>
      <div><span>持仓内有效信号</span><strong>${refreshStartupOnSameSide ? "重置启动计时，不加仓" : "跳过，不刷新"}</strong></div>
      <div><span>信号 K 线</span><strong>${String(profileValue(profile, "use_closed_bar", "true")).toLowerCase() === "false" ? "最新 K 线" : "已收完 K 线"}</strong></div>
    </div>
    <div class="strategy-rule-list">${sections}</div>
    <p class="strategy-footnote">这里解释的是信号与重复开仓逻辑；实际盈亏还会受到左侧风控参数、手续费、滑点和行情数据质量影响。</p>
  `;
}

function openStrategyDetails() {
  renderStrategyDetails();
  state.strategyDetailsReturnFocus = document.activeElement;
  $("strategy-details-modal").classList.remove("is-hidden");
  document.body.classList.add("modal-open");
  $("strategy-details-close").focus();
}

function closeStrategyDetails() {
  $("strategy-details-modal").classList.add("is-hidden");
  document.body.classList.remove("modal-open");
  state.strategyDetailsReturnFocus?.focus?.();
}

async function refreshEstimate() {
  try {
    const payload = await fetchJson("/api/backtests/estimate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildPayload()),
    });
    const cache = payload.cache;
    const barsLabel = payload.estimated_bars == null ? "滚动数据窗口" : `${formatNumber(payload.estimated_bars, 0)} 根 K 线`;
    const cacheStatus = cache.complete ? '<span class="ok">缓存完整</span>' : cache.exists ? '<span class="warn">缓存不完整，将自动补齐</span>' : '<span class="warn">没有本地缓存，首次运行需要下载</span>';
    $("data-estimate").innerHTML = `
      <div class="estimate-item"><span>目标数据</span><strong>${escapeHtml(barsLabel)}</strong></div>
      <div class="estimate-item"><span>参数规模</span><strong>${formatNumber(payload.combinations, 0)} 个组合</strong></div>
      <div class="estimate-item"><span>本地缓存 · ${formatBytes(cache.size_bytes)}</span><strong>${cacheStatus}</strong><small>${escapeHtml(cache.first_time || "--")} 至 ${escapeHtml(cache.last_time || "--")}</small></div>
      <div class="estimate-item"><span>执行方式</span><strong>${escapeHtml(payload.execution_class)}</strong></div>
    `;
    $("form-error").textContent = "";
  } catch (error) {
    $("data-estimate").innerHTML = `<div class="estimate-item"><span>配置检查未通过</span><strong class="warn">${escapeHtml(error.message)}</strong></div>`;
  }
}

async function submitRun() {
  const button = $("run-button");
  button.disabled = true;
  const previousLabel = button.innerHTML;
  button.innerHTML = '<span>正在创建任务…</span><b>···</b>';
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
    button.innerHTML = previousLabel;
  }
}

async function refreshRuns() {
  try {
    const payload = await fetchJson("/api/backtests/runs?limit=30");
    state.runs = payload.runs;
    refreshBaselineOptions();
    renderRuns();
    const active = state.activeRunId && state.runs.find((item) => item.run_id === state.activeRunId);
    if (active?.status === "succeeded" && state.loadedResultRunId !== active.run_id) await loadResult(active.run_id);
  } catch (error) {
    $("run-list").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function refreshBaselineOptions() {
  const select = $("path-baseline-run-select");
  const current = select.value;
  const baselines = state.runs.filter((run) => run.workflow === "signal_path" && run.action === "baseline" && run.status === "succeeded");
  select.innerHTML = '<option value="">重新生成（不复用）</option>' + baselines.map((run) => `<option value="${escapeHtml(run.run_id)}">${escapeHtml(run.name || run.run_id)} · ${escapeHtml(String(run.created_at || "").slice(0,19))}</option>`).join("");
  if (baselines.some((run) => run.run_id === current)) select.value = current;
  else if (baselines.length && $("path-action-select").value === "matrix") select.value = baselines[0].run_id;
}

function renderRuns() {
  if (!state.runs.length) {
    $("run-list").innerHTML = '<div class="empty-state"><span>↗</span><strong>还没有实验任务</strong><small>完成上方配置后，点击“开始回测实验”。</small></div>';
    return;
  }
  $("run-list").innerHTML = state.runs.map((run) => `
    <button class="run-item ${run.run_id === state.activeRunId ? "is-active" : ""}" data-run-id="${escapeHtml(run.run_id)}">
      <div class="run-title"><span>${escapeHtml(run.name || run.run_id)}</span><span class="status-dot status-${run.status}">${statusLabel(run.status)}</span></div>
      <div class="run-kind">${run.workflow === "signal_path" ? run.action === "matrix" ? "PATH · HEATMAP" : "PATH · DATASET" : "STANDARD BACKTEST"}</div>
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
  state.activeResult = result;
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
  renderArtifacts(result);
  if (result.type === "signal_path_baseline" || result.type === "signal_path_matrix") {
    renderPathResult(result);
    return;
  }
  $("result-table-section").classList.remove("is-hidden");
  document.querySelector(".heatmap-metric").classList.add("is-hidden");
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

function renderPathResult(result) {
  document.querySelector(".heatmap-metric").classList.remove("is-hidden");
  const best = result.best || {};
  $("result-title").textContent = result.name || "信号路径研究";
  const badge = $("result-verdict");
  badge.textContent = best.verdict || (result.type === "signal_path_baseline" ? "基准完成" : "--");
  badge.className = `verdict-badge ${String(best.verdict || "").includes("孤点") ? "caution" : String(best.verdict || "").includes("不通过") ? "fail" : ""}`;
  $("score-explanation").textContent = result.score_explanation || "";

  if (result.type === "signal_path_baseline") {
    const metrics = [
      ["闭合路径", formatNumber(best.closed_episode_count, 0)],
      ["有效信号", formatNumber(best.qualified_signal_count, 0)],
      ["同向信号", formatNumber(best.same_side_signal_count, 0)],
      ["模型样本", formatNumber(best.llm_research_episode_count, 0)],
      ["研究+验证基准收益", formatPct(best.return_pct)],
      ["最大回撤", formatPct(best.max_drawdown_pct)],
      ["盈利因子", formatNumber(best.profit_factor)],
      ["中位持仓", `${formatNumber(best.median_holding_bars, 0)} 根`],
      ["中位 MFE", best.median_mfe_atr == null ? "--" : `${formatNumber(best.median_mfe_atr)} ATR`],
      ["中位 MAE", best.median_mae_atr == null ? "--" : `${formatNumber(best.median_mae_atr)} ATR`],
      ["未闭合路径", formatNumber(best.open_episode_count, 0)],
    ];
    $("metric-grid").innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("");
    $("split-grid").innerHTML = datasetSplitItems(result.dataset);
    $("heatmap-section").classList.add("is-hidden");
    $("result-table-section").classList.add("is-hidden");
    return;
  }

  const metrics = [
    ["稳定区评分", formatNumber(best.plateau_score)],
    ["验证段收益", formatPct(best.validation_return_pct)],
    ["研究段收益", formatPct(best.research_return_pct)],
    [result.heatmap?.test_revealed ? "总收益" : "研究+验证收益", formatPct(best.return_pct)],
    ["最大回撤", formatPct(best.max_drawdown_pct)],
    ["盈利因子", formatNumber(best.profit_factor)],
    ["邻域盈利", formatPct(Number(best.positive_neighbor_ratio || 0) * 100)],
    ["稳定区大小", formatNumber(best.region_size, 0)],
    ["交易数", formatNumber(best.trade_count, 0)],
    ["双触发 K 线", formatNumber(best.ambiguous_bar_count, 0)],
  ];
  $("metric-grid").innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("");
  const testLabel = result.heatmap?.test_revealed ? formatPct(best.test_return_pct) : "未揭盲";
  $("split-grid").innerHTML = [
    ["研究段 60%", formatPct(best.research_return_pct)],
    ["验证段 20%", formatPct(best.validation_return_pct)],
    ["最终测试 20%", testLabel],
    ["邻域最差验证收益", formatPct(best.neighbor_min_validation_return_pct)],
    ...Object.entries(best.yearly_returns_pct || {}).map(([year, value]) => [`${year} 年`, formatPct(value)]),
  ].map(([label, value]) => `<div class="split-item">${label}<strong>${value}</strong></div>`).join("");
  $("result-table-section").classList.remove("is-hidden");
  renderPathTable(result.rows || [], Boolean(result.heatmap?.test_revealed));
  renderPathHeatmap(result);
}

function datasetSplitItems(dataset = {}) {
  const splits = dataset.splits || {};
  return [
    ["数据集编号", escapeHtml(dataset.dataset_id || "--")],
    ["K 线数量", formatNumber(dataset.bar_count, 0)],
    ["研究段结束", formatTimestamp(splits.research_end)],
    ["验证段结束", formatTimestamp(splits.validation_end)],
  ].map(([label, value]) => `<div class="split-item">${label}<strong>${value}</strong></div>`).join("");
}

function renderArtifacts(result) {
  const artifacts = result.artifacts || [];
  $("artifact-section").classList.toggle("is-hidden", !artifacts.length);
  if (!artifacts.length) {
    $("artifact-list").innerHTML = "";
    return;
  }
  $("artifact-list").innerHTML = artifacts.map((artifact) => {
    const path = String(artifact.name || "").split("/").map(encodeURIComponent).join("/");
    const href = `/api/backtests/runs/${encodeURIComponent(result.run_id)}/artifacts/${path}`;
    return `<a class="artifact-item" href="${href}"><span><strong>${escapeHtml(artifact.label || artifact.name)}</strong><small>${escapeHtml(artifact.name)}</small></span><b>${formatBytes(artifact.size_bytes)} ↓</b></a>`;
  }).join("");
}

function renderPathTable(rows, testRevealed) {
  const table = $("result-table");
  table.innerHTML = `<thead><tr><th>排名</th><th>结论</th><th>止损</th><th>止盈</th><th>稳定区</th><th>邻域盈利</th><th>验证收益</th><th>研究收益</th><th>${testRevealed ? "测试收益" : "测试段"}</th><th>回撤</th><th>PF</th><th>交易数</th><th></th></tr></thead><tbody>${rows.slice(0, 150).map((row, index) => `
    <tr class="${row.plateau ? "plateau-row" : ""}">
      <td>#${row.rank}</td><td>${escapeHtml(row.verdict)}</td>
      <td>${formatNumber(row.parameters?.stop_loss)}</td><td>${formatNumber(row.parameters?.take_profit)}</td>
      <td>${row.region_id ? `R${row.region_id} · ${row.region_size} 格` : "--"}</td>
      <td>${formatPct(Number(row.positive_neighbor_ratio || 0) * 100)}</td>
      <td>${formatPct(row.validation_return_pct)}</td><td>${formatPct(row.research_return_pct)}</td>
      <td>${testRevealed ? formatPct(row.test_return_pct) : "未揭盲"}</td>
      <td>${formatPct(row.max_drawdown_pct)}</td><td>${formatNumber(row.profit_factor)}</td><td>${formatNumber(row.trade_count,0)}</td>
      <td><button class="mini-button" data-path-row-index="${index}">带回参数</button></td>
    </tr>`).join("")}</tbody>`;
  table.querySelectorAll("[data-path-row-index]").forEach((button) => button.addEventListener("click", () => refillPathCandidate(rows[Number(button.dataset.pathRowIndex)])));
}

function refillPathCandidate(row) {
  $("workflow-select").value = "signal_path";
  $("path-action-select").value = "matrix";
  applyWorkflow();
  $("path-stop-values-input").value = row.parameters?.stop_loss ?? "";
  $("path-take-values-input").value = row.parameters?.take_profit ?? "";
  $("path-stop-unit-select").value = row.parameters?.unit || "atr";
  $("path-max-reentries-input").value = row.parameters?.max_reentries ?? 0;
  $("path-reentry-cooldown-input").value = row.parameters?.reentry_cooldown_bars ?? 0;
  $("path-intrabar-policy-select").value = row.parameters?.intrabar_policy || "stop_first";
  $("experiment-name").value = `${$("experiment-name").value.replace(/ · 候选复测$/, "")} · 候选复测`;
  scheduleEstimate();
  document.querySelector(".builder-card").scrollIntoView({ behavior: "smooth", block: "start" });
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

function renderPathHeatmap(result) {
  const heatmap = result.heatmap || {};
  const xs = heatmap.x_values || [];
  const ys = heatmap.y_values || [];
  const section = $("heatmap-section");
  if (!xs.length || !ys.length) {
    section.classList.add("is-hidden");
    return;
  }
  section.classList.remove("is-hidden");
  const metric = $("heatmap-metric-select").value || heatmap.default_metric || "validation_return_pct";
  const byCoordinate = new Map((result.rows || []).map((row) => [
    `${row.parameters?.stop_loss}|${row.parameters?.take_profit}`,
    row,
  ]));
  const numericValues = (result.rows || []).map((row) => Number(row[metric])).filter(Number.isFinite);
  const scale = heatScale(metric, numericValues);
  const unit = ({atr:"ATR",percent:"%",points:"点"})[heatmap.unit] || heatmap.unit || "";
  const cells = [
    `<div class="heat-axis heat-corner">止盈 ↓ / 止损 →<small>${escapeHtml(unit)}</small></div>`,
    ...xs.map((x) => `<div class="heat-axis">${formatNumber(x)} <small>${escapeHtml(unit)}</small></div>`),
  ];
  ys.forEach((y) => {
    cells.push(`<div class="heat-axis">${formatNumber(y)} <small>${escapeHtml(unit)}</small></div>`);
    xs.forEach((x) => {
      const row = byCoordinate.get(`${x}|${y}`);
      const value = Number(row?.[metric]);
      const display = metric === "positive_neighbor_ratio"
        ? formatPct(value * 100)
        : metric.includes("pct") ? formatPct(value) : formatNumber(value);
      const title = row ? [
        `止损 ${x}${unit} / 止盈 ${y}${unit}`,
        `研究收益 ${formatPct(row.research_return_pct)}`,
        `验证收益 ${formatPct(row.validation_return_pct)}`,
        `最大回撤 ${formatPct(row.max_drawdown_pct)}`,
        `邻域盈利 ${formatPct(Number(row.positive_neighbor_ratio || 0) * 100)}`,
        row.region_id ? `稳定区 R${row.region_id}，${row.region_size} 格` : "不属于稳定区",
      ].join("\n") : "无结果";
      cells.push(`<div class="heat-cell ${row?.plateau ? "is-plateau" : ""}" style="background:${heatColor(metric, value, scale)}" title="${escapeHtml(title)}"><strong>${display}</strong><small>${row?.region_id ? `R${row.region_id}` : ""}</small></div>`);
    });
  });
  const plateauRows = (result.rows || []).filter((row) => row.plateau);
  const regions = new Set(plateauRows.map((row) => row.region_id).filter(Boolean));
  const ambiguous = Math.max(0, ...(result.rows || []).map((row) => Number(row.ambiguous_bar_count || 0)));
  $("heatmap-summary").innerHTML = `
    <span>数据集 <strong>${escapeHtml(result.dataset?.dataset_id || "--")}</strong></span>
    <span><strong>${plateauRows.length}</strong> 个稳定格</span>
    <span><strong>${regions.size}</strong> 片连续区域</span>
    <span><strong>${ambiguous}</strong> 根双触发 K 线</span>
    <span>测试段：<strong>${heatmap.test_revealed ? "已揭盲" : "保持隐藏"}</strong></span>
  `;
  const map = $("heatmap");
  map.style.gridTemplateColumns = `repeat(${xs.length + 1}, minmax(96px, 1fr))`;
  map.innerHTML = cells.join("");
}

function heatScale(metric, values) {
  if (!values.length) return { min: 0, max: 1, abs: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  return { min, max, abs: Math.max(Math.abs(min), Math.abs(max), 1e-9) };
}

function heatColor(metric, value, scale) {
  if (!Number.isFinite(value)) return "rgba(80,92,102,.28)";
  if (metric === "max_drawdown_pct") {
    const ratio = scale.max === scale.min ? .5 : (value - scale.min) / (scale.max - scale.min);
    return `hsl(${142 - ratio * 136} 52% ${25 + (1-ratio) * 12}%)`;
  }
  if (metric === "profit_factor") {
    const centered = Math.max(-1, Math.min(1, (value - 1) / Math.max(scale.max - 1, 1)));
    const hue = centered >= 0 ? 142 : 6;
    return `hsl(${hue} ${34 + Math.abs(centered) * 34}% ${23 + Math.abs(centered) * 12}%)`;
  }
  if (metric === "positive_neighbor_ratio") {
    const ratio = Math.max(0, Math.min(1, value));
    return `hsl(${8 + ratio * 134} ${38 + ratio * 28}% ${23 + ratio * 13}%)`;
  }
  const ratio = Math.max(-1, Math.min(1, value / scale.abs));
  const hue = ratio >= 0 ? 142 : 6;
  return `hsl(${hue} ${34 + Math.abs(ratio) * 34}% ${22 + Math.abs(ratio) * 14}%)`;
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
  document.querySelector(".builder-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderChart(payload) {
  $("chart-section").classList.remove("is-hidden");
  $("chart-note").textContent = `${formatNumber(payload.source_bar_count,0)} 根压缩为 ${formatNumber(payload.preview_bar_count,0)} 根预览`;
  const container = $("backtest-chart");
  container.innerHTML = "";
  if (state.chart) state.chart.remove();
  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 500, layout: { background: { color: "#090e13" }, textColor: "#7f919f" },
    grid: { vertLines: { color: "rgba(184,205,224,.055)" }, horzLines: { color: "rgba(184,205,224,.055)" } },
    timeScale: { borderColor: "rgba(184,205,224,.12)", timeVisible: true }, rightPriceScale: { borderColor: "rgba(184,205,224,.12)" },
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
function formatBytes(value) { const bytes = Number(value || 0); if (!bytes) return "0 B"; if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function formatTimestamp(value) { const timestamp = Number(value); if (!Number.isFinite(timestamp)) return "--"; return new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false }); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]); }

function profileDisplayName(profile) {
  const symbol = profileValue(profile, "symbol", "--");
  const duration = strategyDurationLabel(profileValue(profile, "duration", 0));
  const start = String(profileValue(profile, "start_time", "")).slice(0, 4);
  const end = String(profileValue(profile, "end_time", "")).slice(0, 4);
  const range = start && end ? `${start}–${end}` : "滚动窗口";
  let variant = "标准基线";
  if (profile.name.includes("signal_path")) variant = "1D Hull/STC 信号路径";
  else if (profile.name.includes("1d_1h_reentry")) variant = "1D/1H 再入场";
  else if (profile.name.includes("legacy")) variant = "历史口径";
  else if (profile.name.includes("latest_month")) variant = "最近一个月";
  else if (profile.name.includes("cached")) variant = "当前缓存口径";
  return `${symbol} · ${duration} · ${range} · ${variant} — ${profile.name}`;
}

async function boot() {
  state.catalog = await fetchJson("/api/backtests/catalog");
  state.catalog.profiles.forEach((profile) => state.profiles.set(profile.name, profile));
  $("profile-select").innerHTML = state.catalog.profiles.map((profile) => `<option value="${escapeHtml(profile.name)}">${escapeHtml(profileDisplayName(profile))}</option>`).join("");
  $("duration-select").innerHTML = state.catalog.durations.map((duration) => `<option value="${duration}">${duration >= 3600 ? `${duration/3600}h` : `${duration/60}m`}</option>`).join("");
  const strategies = state.catalog.strategies.flatMap((item) => [item.name, ...(item.aliases || [])].map((name) => ({ name, item })));
  $("strategy-select").innerHTML = strategies.map(({name, item}) => {
    const title = item.details?.title || name;
    return `<option value="${escapeHtml(name)}">${escapeHtml(`${title} — ${name}`)}</option>`;
  }).join("");
  $("profile-select").addEventListener("change", () => populateProfile($("profile-select").value));
  $("workflow-select").addEventListener("change", applyWorkflow);
  $("path-action-select").addEventListener("change", applyWorkflow);
  ["path-context-bars-input","path-baseline-run-select","path-stop-unit-select","path-stop-values-input","path-take-values-input","path-max-reentries-input","path-reentry-cooldown-input","path-intrabar-policy-select","path-reveal-test-input"].forEach((id) => {
    $(id).addEventListener("input", scheduleEstimate);
    $(id).addEventListener("change", scheduleEstimate);
  });
  $("heatmap-metric-select").addEventListener("change", () => {
    if (state.activeResult?.type === "signal_path_matrix") renderPathHeatmap(state.activeResult);
  });
  ["provider-select","symbol-input","duration-select","start-time-input","end-time-input","strategy-select","initial-equity-input","fee-rate-input","slippage-rate-input","cache-enabled-input"].forEach((id) => $(id).addEventListener("change", scheduleEstimate));
  $("strategy-select").addEventListener("change", updateStrategyHint);
  $("strategy-details-button").addEventListener("click", openStrategyDetails);
  $("strategy-details-close").addEventListener("click", closeStrategyDetails);
  $("strategy-details-modal").addEventListener("click", (event) => {
    if (event.target === $("strategy-details-modal")) closeStrategyDetails();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("strategy-details-modal").classList.contains("is-hidden")) closeStrategyDetails();
  });
  gridInputs().forEach((input) => input.addEventListener("input", scheduleEstimate));
  gridInputs().forEach((input) => { input.placeholder ||= "单值或 起始:结束:步长"; });
  $("run-button").addEventListener("click", submitRun);
  $("refresh-runs-button").addEventListener("click", refreshRuns);
  const recommended = state.profiles.has("btc_5m_signal_path")
    ? "btc_5m_signal_path"
    : state.profiles.has("btc_5m_range_cached_1d_1h_reentry")
      ? "btc_5m_range_cached_1d_1h_reentry"
      : state.profiles.has("btc_5m_range_cached") ? "btc_5m_range_cached" : state.catalog.profiles[0]?.name || "";
  $("profile-select").value = recommended;
  populateProfile(recommended);
  applyWorkflow();
  await refreshRuns();
  state.pollTimer = setInterval(refreshRuns, 2500);
}

boot().catch((error) => { $("form-error").textContent = error.message; });
