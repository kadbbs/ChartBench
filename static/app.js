const state = {
  config: null,
  activeProvider: "",
  activeSymbol: "",
  activeDurationSeconds: null,
  activeBarMode: "time",
  activeRangeTicks: 10,
  activeBrickLength: 10000,
  selectedIndicators: [],
  indicatorParams: {},
  charts: [],
  seriesByKey: new Map(),
  seriesChartByKey: new Map(),
  seriesDataByKey: new Map(),
  seriesIndexByKey: new Map(),
  primarySeriesKeyByPane: new Map(),
  bandPrimitiveByKey: new Map(),
  currentPriceLine: null,
  renderedViewportSignature: "",
  renderedSymbol: "",
  hasFitted: false,
  isSyncingCrosshair: false,
  refreshTimerId: null,
  snapshotRequestId: 0,
  configRequestId: 0,
  snapshotRefreshInFlight: false,
  requestedDataLength: null,
  wsConnection: null,
  wsReconnectTimerId: null,
  wsActiveSignature: "",
  wsConnectingSignature: "",
  wsLastMessageAt: 0,
  wsOpenedAt: 0,
  wsMonitorTimerId: null,
  streamVersion: null,
  wsActualToSyntheticTime: new Map(),
  wsSyntheticToActualTime: new Map(),
  wsMaxSyntheticTime: null,
  wsMaxActualTimeMs: null,
  indicatorSyncTimerId: null,
  terminalToggles: {
    text: true,
    candle: true,
    oi: true,
    nl: true,
    ns: true,
    vwap: true,
  },
  runtimeIndicators: [],
  watchlistMode: "all",
  watchlistSymbols: [],
};

const DEFAULT_VISIBLE_BARS = 120;
const MIN_VISIBLE_DATA_BARS = 60;
const RANGE_RIGHT_PADDING_BARS = 8;
const PRICE_RANGE_TOP_PADDING = 0.1;
const PRICE_RANGE_BOTTOM_PADDING = 0.14;
const PRICE_RANGE_BOTTOM_PADDING_WITH_VOLUME_PANE = 0.02;
const RIGHT_PRICE_SCALE_MIN_WIDTH = 72;
const RENKO_DEFAULT_TICKS = 5;
const RANGE_DEFAULT_TICKS = 10;
const VOLUME_PANE_ID = "__volume__";
const WS_RECONNECT_MS = 2000;
const INDICATOR_SYNC_MS = 1200;
const WS_STALE_MS = 20000;
const TERMINAL_TEMPLATE_STORAGE_KEY = "qh_terminal_template_v1";
const WATCHLIST_STORAGE_KEY = "qh_symbol_watchlist_v1";

function paneLabelConfig(paneId) {
  return [];
}

function updatePaneLabelPositions() {
  state.charts.forEach((entry) => {
    if (!entry.labelOverlay || !entry.labelConfig?.length) {
      return;
    }
    const seriesKey = primarySeriesKeyForPane(entry.paneId);
    const series = seriesKey ? state.seriesByKey.get(seriesKey) : null;
    if (!series || typeof series.priceToCoordinate !== "function") {
      return;
    }
    entry.labelElements.forEach((element, index) => {
      const config = entry.labelConfig[index];
      const coordinate = series.priceToCoordinate(config.value);
      if (!Number.isFinite(coordinate)) {
        element.style.opacity = "0";
        return;
      }
      element.style.opacity = "1";
      element.style.top = `${coordinate}px`;
    });
  });
}

function formatAxisTimeLabel(time) {
  const resolved = resolveDisplayTime(time);
  if (resolved) {
    const [datePart, timePart = ""] = resolved.split(" ");
    const [, month = "", day = ""] = datePart.split("-");
    return timePart ? `${month}-${day} ${timePart.slice(0, 5)}` : `${month}-${day}`;
  }
  if (typeof time === "number") {
    return new Date(time * 1000).toLocaleString("zh-CN", {
      hour12: false,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (time && typeof time === "object" && "year" in time) {
    const month = String(time.month).padStart(2, "0");
    const day = String(time.day).padStart(2, "0");
    return `${month}-${day}`;
  }
  return "";
}

function resolveDisplayTime(time) {
  if (typeof time !== "number") {
    return null;
  }

  const candidates = [
    String(time),
    String(Math.round(time)),
    String(Math.floor(time)),
    String(Math.ceil(time)),
  ];

  if (Math.abs(time) < 1e9) {
    candidates.push(
      String(Math.round(time * 1000)),
      String(Math.floor(time * 1000)),
      String(Math.ceil(time * 1000))
    );
  }

  if (Math.abs(time) > 1e11) {
    candidates.push(
      String(Math.round(time / 1000)),
      String(Math.floor(time / 1000)),
      String(Math.ceil(time / 1000))
    );
  }

  for (const candidate of candidates) {
    const label = state.timeLabels.get(candidate);
    if (label) {
      return label;
    }
  }

  if (state.timeLabels.size > 0) {
    let nearestLabel = null;
    let nearestDiff = Number.POSITIVE_INFINITY;
    const maxDiff = 2;
    for (const [key, label] of state.timeLabels.entries()) {
      const numericKey = Number(key);
      if (!Number.isFinite(numericKey) || !label) {
        continue;
      }
      const diff = Math.abs(numericKey - time);
      if (diff < nearestDiff) {
        nearestDiff = diff;
        nearestLabel = label;
      }
    }
    if (nearestLabel && nearestDiff <= maxDiff) {
      return nearestLabel;
    }
  }

  return null;
}

class LineBandPrimitiveRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const segments = this._source.coordinateSegments();
    if (!segments.length) {
      return;
    }

    target.useMediaCoordinateSpace((scope) => {
      const { context } = scope;
      context.save();
      context.fillStyle = this._source.fillColor();

      segments.forEach((segment) => {
        if (segment.length < 2) {
          return;
        }
        context.beginPath();
        context.moveTo(segment[0].x, segment[0].y1);
        for (let index = 1; index < segment.length; index += 1) {
          context.lineTo(segment[index].x, segment[index].y1);
        }
        for (let index = segment.length - 1; index >= 0; index -= 1) {
          context.lineTo(segment[index].x, segment[index].y2);
        }
        context.closePath();
        context.fill();
      });

      context.restore();
    });
  }
}

class LineBandPrimitivePaneView {
  constructor(source) {
    this._source = source;
    this._renderer = new LineBandPrimitiveRenderer(source);
  }

  zOrder() {
    return "bottom";
  }

  renderer() {
    return this._renderer;
  }
}

class LineBandPrimitive {
  constructor(fillColor) {
    this._fillColor = fillColor;
    this._chart = null;
    this._primarySeries = null;
    this._secondarySeries = null;
    this._primaryData = [];
    this._secondaryData = [];
    this._requestUpdate = null;
    this._paneViews = [new LineBandPrimitivePaneView(this)];
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._primarySeries = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._primarySeries = null;
    this._requestUpdate = null;
  }

  updateAllViews() {
    this._requestUpdate?.();
  }

  paneViews() {
    return this._paneViews;
  }

  fillColor() {
    return this._fillColor;
  }

  setFillColor(fillColor) {
    this._fillColor = fillColor;
    this.updateAllViews();
  }

  setSecondarySeries(series) {
    this._secondarySeries = series;
    this.updateAllViews();
  }

  setData(primaryData, secondaryData) {
    this._primaryData = primaryData || [];
    this._secondaryData = secondaryData || [];
    this.updateAllViews();
  }

  coordinateSegments() {
    if (!this._chart || !this._primarySeries || !this._secondarySeries) {
      return [];
    }

    const timeScale = this._chart.timeScale();
    const secondaryByTime = new Map(
      this._secondaryData
        .filter((point) => typeof point?.value === "number")
        .map((point) => [String(point.time), point.value])
    );

    const segments = [];
    let currentSegment = [];

    const flush = () => {
      if (currentSegment.length) {
        segments.push(currentSegment);
        currentSegment = [];
      }
    };

    this._primaryData.forEach((point) => {
      if (typeof point?.value !== "number") {
        flush();
        return;
      }

      const pairedValue = secondaryByTime.get(String(point.time));
      if (typeof pairedValue !== "number") {
        flush();
        return;
      }

      const x = timeScale.timeToCoordinate(point.time);
      const y1 = this._primarySeries.priceToCoordinate(point.value);
      const y2 = this._secondarySeries.priceToCoordinate(pairedValue);
      if (!Number.isFinite(x) || !Number.isFinite(y1) || !Number.isFinite(y2)) {
        flush();
        return;
      }

      currentSegment.push({ x, y1, y2 });
    });

    flush();
    return segments;
  }
}

const els = {
  title: document.getElementById("page-title"),
  provider: document.getElementById("provider-name"),
  providerSelect: document.getElementById("provider-select"),
  providerHint: document.getElementById("provider-hint"),
  symbol: document.getElementById("symbol-name"),
  duration: document.getElementById("duration-name"),
  symbolSelect: document.getElementById("symbol-select"),
  watchlistTabFavorites: document.getElementById("watchlist-tab-favorites"),
  watchlistTabAll: document.getElementById("watchlist-tab-all"),
  watchlistAddCurrent: document.getElementById("watchlist-add-current"),
  watchlistRemoveCurrent: document.getElementById("watchlist-remove-current"),
  barModeSelect: document.getElementById("bar-mode-select"),
  durationSelect: document.getElementById("duration-select"),
  barSizeLabel: document.getElementById("bar-size-label"),
  rangeTicksInput: document.getElementById("range-ticks-input"),
  historySizeLabel: document.getElementById("history-size-label"),
  brickLengthInput: document.getElementById("brick-length-input"),
  lastPrice: document.getElementById("last-price"),
  lastUpdate: document.getElementById("last-update"),
  cursorTime: document.getElementById("cursor-time"),
  contractDetailCard: document.getElementById("contract-detail-card"),
  detailFirstTick: document.getElementById("detail-first-tick"),
  detailLastTick: document.getElementById("detail-last-tick"),
  detailTickCount: document.getElementById("detail-tick-count"),
  detailPriceTick: document.getElementById("detail-price-tick"),
  detailContractMonth: document.getElementById("detail-contract-month"),
  detailVolumeMultiple: document.getElementById("detail-volume-multiple"),
  indicatorForm: document.getElementById("indicator-form"),
  chartStack: document.getElementById("chart-stack"),
  toolbarProvider: document.getElementById("toolbar-provider"),
  toolbarSymbol: document.getElementById("toolbar-symbol"),
  toolbarDuration: document.getElementById("toolbar-duration"),
  toolbarBarMode: document.getElementById("toolbar-bar-mode"),
  toolbarRangeTicks: document.getElementById("toolbar-range-ticks"),
  toolbarBrickLength: document.getElementById("toolbar-brick-length"),
  toggleText: document.getElementById("toggle-text"),
  toggleCandle: document.getElementById("toggle-candle"),
  toggleOi: document.getElementById("toggle-oi"),
  toggleNl: document.getElementById("toggle-nl"),
  toggleNs: document.getElementById("toggle-ns"),
  toggleVwap: document.getElementById("toggle-vwap"),
  saveTemplate: document.getElementById("toolbar-save-template"),
  resetTemplate: document.getElementById("toolbar-reset-template"),
  metaContract: document.getElementById("meta-contract"),
  metaStatus: document.getElementById("meta-status"),
  error: document.getElementById("error-message"),
};

state.timeLabels = new Map();

const chartTheme = {
  layout: {
    background: { type: "solid", color: "#16171c" },
    textColor: "#8d93a5",
    fontFamily: "IBM Plex Sans, PingFang SC, Microsoft YaHei, sans-serif",
  },
  grid: {
    vertLines: { color: "rgba(133, 137, 153, 0.08)" },
    horzLines: { color: "rgba(133, 137, 153, 0.08)" },
  },
  crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: { color: "#7f86a3", labelBackgroundColor: "#2e3344" },
    horzLine: { color: "#7f86a3", labelBackgroundColor: "#2e3344" },
  },
  rightPriceScale: {
    borderColor: "rgba(133, 137, 153, 0.18)",
    autoScale: true,
    minimumWidth: RIGHT_PRICE_SCALE_MIN_WIDTH,
    scaleMargins: {
      top: 0.16,
      bottom: 0.2,
    },
  },
  timeScale: {
    borderColor: "rgba(133, 137, 153, 0.18)",
    timeVisible: true,
    secondsVisible: false,
    rightOffset: 10,
    barSpacing: 10,
    minBarSpacing: 4,
    tickMarkMaxCharacterLength: 18,
    ticksVisible: true,
    tickMarkFormatter: (time) => formatAxisTimeLabel(time),
  },
  handleScroll: {
    mouseWheel: true,
    pressedMouseMove: true,
    horzTouchDrag: true,
    vertTouchDrag: false,
  },
  handleScale: {
    mouseWheel: true,
    pinch: true,
    axisPressedMouseMove: {
      time: true,
      price: true,
    },
  },
  localization: {
    locale: "zh-CN",
    dateFormat: "yyyy-MM-dd",
    timeFormatter: (time) => {
      const resolved = resolveDisplayTime(time);
      if (resolved) {
        return resolved;
      }
      if (state.activeBarMode === "time") {
        return "";
      }
      if (typeof time === "number") {
        return new Date(time * 1000).toLocaleString("zh-CN", {
          hour12: false,
        });
      }
      if (time && typeof time === "object" && "year" in time) {
        const month = String(time.month).padStart(2, "0");
        const day = String(time.day).padStart(2, "0");
        return `${time.year}-${month}-${day}`;
      }
      return "";
    },
  },
};

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function shouldUseBrowserPush(provider = getRequestedProvider(), barMode = getRequestedBarMode()) {
  return ["tianqin", "binance", "bitget"].includes(provider) && barMode === "time";
}

function wsIntervalForProvider(provider, durationSeconds) {
  if (provider === "tianqin" && durationSeconds > 0 && durationSeconds <= 86400) {
    return String(durationSeconds);
  }
  const providerIntervals = {
    binance: {
      60: "1m",
      180: "3m",
      300: "5m",
      900: "15m",
      1800: "30m",
      3600: "1h",
      7200: "2h",
      14400: "4h",
      21600: "6h",
      28800: "8h",
      43200: "12h",
      86400: "1d",
    },
    bitget: {
      60: "1m",
      180: "3m",
      300: "5m",
      900: "15m",
      1800: "30m",
      3600: "1H",
      7200: "2H",
      14400: "4H",
      21600: "6H",
      43200: "12H",
      86400: "1D",
    },
  };
  return providerIntervals[provider]?.[durationSeconds] || null;
}

function requestedWsSignature() {
  const provider = getRequestedProvider();
  const barMode = getRequestedBarMode();
  const durationSeconds = getRequestedDuration();
  const symbol = getRequestedSymbol();
  if (!shouldUseBrowserPush(provider, barMode)) {
    return "";
  }
  const interval = wsIntervalForProvider(provider, durationSeconds);
  if (!interval || !symbol) {
    return "";
  }
  const indicatorSignature = state.selectedIndicators
    .map((indicatorId) => `${indicatorId}:${JSON.stringify(state.indicatorParams[indicatorId] || {})}`)
    .join(",");
  return [
    provider,
    symbol,
    durationSeconds,
    barMode,
    getRequestedRangeTicks(),
    getRequestedBrickLength(),
    currentRequestedDataLength(),
    indicatorSignature,
  ].join("|");
}

function clearRealtimeReconnect() {
  if (state.wsReconnectTimerId) {
    window.clearTimeout(state.wsReconnectTimerId);
    state.wsReconnectTimerId = null;
  }
}

function stopRealtimeMonitor() {
  if (state.wsMonitorTimerId) {
    window.clearInterval(state.wsMonitorTimerId);
    state.wsMonitorTimerId = null;
  }
}

function startRealtimeMonitor() {
  stopRealtimeMonitor();
  state.wsMonitorTimerId = window.setInterval(async () => {
    if (!shouldUseBrowserPush()) {
      return;
    }
    const isConnected = state.wsConnection && state.wsConnection.readyState === EventSource.OPEN;
    const referenceTime = state.wsLastMessageAt || state.wsOpenedAt || 0;
    const lastMessageAge = referenceTime > 0 ? Date.now() - referenceTime : Number.POSITIVE_INFINITY;
    if (isConnected && lastMessageAge <= WS_STALE_MS) {
      return;
    }
    try {
      disconnectRealtimeStream();
      connectRealtimeStream();
    } catch (error) {
      els.error.textContent = error.message;
    }
  }, 5000);
}

function disconnectRealtimeStream() {
  clearRealtimeReconnect();
  stopRealtimeMonitor();
  if (state.indicatorSyncTimerId) {
    window.clearTimeout(state.indicatorSyncTimerId);
    state.indicatorSyncTimerId = null;
  }
  if (state.wsConnection) {
    const socket = state.wsConnection;
    state.wsConnection = null;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    if (typeof socket.close === "function") {
      socket.close();
    }
  }
  state.wsActiveSignature = "";
  state.wsConnectingSignature = "";
  state.wsLastMessageAt = 0;
  state.wsOpenedAt = 0;
  state.streamVersion = null;
}

function scheduleRealtimeReconnect(signature) {
  clearRealtimeReconnect();
  if (!signature || signature !== requestedWsSignature()) {
    return;
  }
  state.wsReconnectTimerId = window.setTimeout(() => {
    state.wsReconnectTimerId = null;
    connectRealtimeStream();
  }, WS_RECONNECT_MS);
}

function formatWsDisplayTime(timestampMs) {
  const date = new Date(timestampMs);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function rebuildWsTimeIndex(snapshot) {
  state.wsActualToSyntheticTime = new Map();
  state.wsSyntheticToActualTime = new Map();
  state.wsMaxSyntheticTime = null;
  state.wsMaxActualTimeMs = null;

  (snapshot.candles || []).forEach((candle) => {
    const syntheticTime = Number(candle.time);
    const actualTimeMs = (snapshot.bar_mode || state.activeBarMode) === "time"
      ? syntheticTime * 1000
      : Date.parse((snapshot.time_labels?.[String(syntheticTime)] || "").replace(" ", "T"));
    if (!Number.isFinite(actualTimeMs)) {
      return;
    }
    state.wsActualToSyntheticTime.set(actualTimeMs, syntheticTime);
    state.wsSyntheticToActualTime.set(syntheticTime, actualTimeMs);
    state.wsMaxSyntheticTime = state.wsMaxSyntheticTime === null ? syntheticTime : Math.max(state.wsMaxSyntheticTime, syntheticTime);
    state.wsMaxActualTimeMs = state.wsMaxActualTimeMs === null ? actualTimeMs : Math.max(state.wsMaxActualTimeMs, actualTimeMs);
  });
}

function resolveSyntheticTime(actualTimeMs) {
  if (getRequestedBarMode() === "time") {
    const syntheticTime = Math.floor(actualTimeMs / 1000);
    const existingActual = state.wsSyntheticToActualTime.get(syntheticTime);
    state.wsActualToSyntheticTime.set(actualTimeMs, syntheticTime);
    state.wsSyntheticToActualTime.set(syntheticTime, actualTimeMs);
    state.wsMaxSyntheticTime = state.wsMaxSyntheticTime === null ? syntheticTime : Math.max(state.wsMaxSyntheticTime, syntheticTime);
    state.wsMaxActualTimeMs = state.wsMaxActualTimeMs === null ? actualTimeMs : Math.max(state.wsMaxActualTimeMs, actualTimeMs);
    return { syntheticTime, isNewBar: existingActual === undefined };
  }
  const existing = state.wsActualToSyntheticTime.get(actualTimeMs);
  if (existing !== undefined) {
    return { syntheticTime: existing, isNewBar: false };
  }
  if (state.wsMaxActualTimeMs !== null && actualTimeMs < state.wsMaxActualTimeMs) {
    return { syntheticTime: null, isNewBar: false };
  }
  const syntheticTime = (state.wsMaxSyntheticTime ?? 0) + 1;
  state.wsActualToSyntheticTime.set(actualTimeMs, syntheticTime);
  state.wsSyntheticToActualTime.set(syntheticTime, actualTimeMs);
  state.wsMaxSyntheticTime = syntheticTime;
  state.wsMaxActualTimeMs = actualTimeMs;
  return { syntheticTime, isNewBar: true };
}

function syncCurrentPriceLine(price, color) {
  const candleSeries = state.seriesByKey.get("candles");
  if (!candleSeries) {
    return;
  }
  if (state.currentPriceLine && typeof state.currentPriceLine.applyOptions === "function") {
    state.currentPriceLine.applyOptions({ price, color });
    return;
  }
  if (state.currentPriceLine && typeof candleSeries.removePriceLine === "function") {
    candleSeries.removePriceLine(state.currentPriceLine);
  }
  state.currentPriceLine = candleSeries.createPriceLine({
    price,
    color,
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title: "现价",
  });
}

function clearCurrentPriceLine() {
  const candleSeries = state.seriesByKey.get("candles");
  if (state.currentPriceLine && typeof candleSeries?.removePriceLine === "function") {
    candleSeries.removePriceLine(state.currentPriceLine);
  }
  state.currentPriceLine = null;
}

function scheduleIndicatorSnapshotSync() {
  if (!shouldUseBrowserPush(state.activeProvider, state.activeBarMode) || state.selectedIndicators.length === 0) {
    return;
  }
  if (state.indicatorSyncTimerId) {
    window.clearTimeout(state.indicatorSyncTimerId);
  }
  state.indicatorSyncTimerId = window.setTimeout(async () => {
    state.indicatorSyncTimerId = null;
    try {
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  }, INDICATOR_SYNC_MS);
}

function applyRealtimeWsCandleUpdate(rawKline) {
  if (!rawKline || typeof rawKline !== "object") {
    return;
  }
  const actualTimeMs = Number(rawKline.t);
  if (!Number.isFinite(actualTimeMs)) {
    return;
  }
  const { syntheticTime, isNewBar } = resolveSyntheticTime(actualTimeMs);
  if (!Number.isFinite(syntheticTime)) {
    return;
  }

  const open = Number(rawKline.o);
  const high = Number(rawKline.h);
  const low = Number(rawKline.l);
  const close = Number(rawKline.c);
  const volume = Number(rawKline.v);
  if (![open, high, low, close].every(Number.isFinite)) {
    return;
  }

  const displayTime = formatWsDisplayTime(actualTimeMs);
  state.timeLabels.set(String(syntheticTime), displayTime);

  const nextCandle = { time: syntheticTime, open, high, low, close };
  const nextVolume = {
    time: syntheticTime,
    value: Number.isFinite(volume) ? volume : 0,
    color: close >= open ? "#089981" : "#f23645",
  };
  const maxLength = currentRequestedDataLength();
  const candles = state.seriesDataByKey.get("candles") || [];
  const removedSynthetic = isNewBar && candles.length >= maxLength
    ? Number(candles[0]?.time)
    : null;
  updateSeriesFromDelta("candles", state.seriesByKey.get("candles"), [nextCandle], maxLength);
  updateSeriesFromDelta("volume", state.seriesByKey.get("volume"), [nextVolume], maxLength);

  if (Number.isFinite(removedSynthetic)) {
    const removedActual = state.wsSyntheticToActualTime.get(removedSynthetic);
    if (removedActual !== undefined) {
      state.wsSyntheticToActualTime.delete(removedSynthetic);
      state.wsActualToSyntheticTime.delete(removedActual);
    }
    state.timeLabels.delete(String(removedSynthetic));
  }

  els.lastPrice.textContent = close.toFixed(2);
  els.lastPrice.style.color = close >= open ? "#089981" : "#f23645";
  els.lastUpdate.textContent = displayTime;
  syncCurrentPriceLine(close, close >= open ? "#089981" : "#f23645");
  updatePaneLabelPositions();

  if (isNewBar) {
    scheduleIndicatorSnapshotSync();
  }
}

function connectRealtimeStream() {
  const signature = requestedWsSignature();
  const provider = getRequestedProvider();
  if (!signature) {
    disconnectRealtimeStream();
    return;
  }
  if (state.wsConnection && state.wsActiveSignature === signature) {
    return;
  }

  disconnectRealtimeStream();
  const params = buildSnapshotParams();
  const socket = new EventSource(`/api/stream?${params.toString()}`);
  state.wsConnection = socket;
  state.wsConnectingSignature = signature;

  socket.onopen = () => {
    if (state.wsConnection !== socket) {
      socket.close();
      return;
    }
    state.wsActiveSignature = signature;
    state.wsConnectingSignature = "";
    state.wsOpenedAt = Date.now();
    els.error.textContent = "";
    if (els.metaStatus) {
      els.metaStatus.textContent = `Backend ${provider} stream connected`;
    }
    startRealtimeMonitor();
  };

  socket.addEventListener("snapshot", (event) => {
    if (state.wsConnection !== socket || state.wsActiveSignature !== signature) {
      return;
    }
    try {
      state.wsLastMessageAt = Date.now();
      applySnapshot(JSON.parse(event.data));
    } catch (error) {
      els.error.textContent = error.message;
    }
  });

  socket.addEventListener("snapshot-delta", async (event) => {
    if (state.wsConnection !== socket || state.wsActiveSignature !== signature) {
      return;
    }
    try {
      state.wsLastMessageAt = Date.now();
      const applied = applySnapshotDelta(JSON.parse(event.data));
      if (!applied) {
        await refreshSnapshot();
      }
    } catch (error) {
      els.error.textContent = error.message;
    }
  });

  socket.addEventListener("heartbeat", () => {
    if (state.wsConnection === socket && state.wsActiveSignature === signature) {
      state.wsLastMessageAt = Date.now();
    }
  });

  socket.addEventListener("stream-error", (event) => {
    if (state.wsConnection !== socket) {
      return;
    }
    try {
      const payload = JSON.parse(event.data || "{}");
      els.error.textContent = payload.error || "后端行情流异常。";
    } catch {
      els.error.textContent = "后端行情流异常。";
    }
  });

  socket.onerror = () => {
    if (state.wsConnection === socket) {
      els.error.textContent = "后端行情流连接异常，浏览器将自动重连。";
    }
  };
}

function syncRealtimeTransport() {
  if (shouldUseBrowserPush()) {
    connectRealtimeStream();
    return;
  }
  disconnectRealtimeStream();
}

function getIndicatorIds() {
  return [...els.indicatorForm.querySelectorAll('input[data-role="indicator-toggle"]:checked')].map((item) => item.value);
}

function getDefaultIndicatorParams(indicators) {
  const defaults = {};
  indicators.forEach((indicator) => {
    defaults[indicator.id] = {};
    (indicator.params || []).forEach((param) => {
      defaults[indicator.id][param.key] = param.default;
    });
  });
  return defaults;
}

function updateIndicatorParamState(indicatorId, key, value) {
  if (!state.indicatorParams[indicatorId]) {
    state.indicatorParams[indicatorId] = {};
  }
  state.indicatorParams[indicatorId][key] = value;
}

function isIndicatorEnabled(indicatorId) {
  return [...els.indicatorForm.querySelectorAll('input[data-role="indicator-toggle"]:checked')].some(
    (item) => item.value === indicatorId
  );
}

function buildDefaultTerminalTemplate() {
  return {
    provider: state.config?.provider || "binance",
    symbol: state.config?.symbol || "KQ.m@SHFE.cu",
    duration_seconds: state.config?.duration_seconds || 60,
    bar_mode: state.config?.bar_mode || "time",
    range_ticks: state.config?.range_ticks || 10,
    brick_length: state.config?.brick_length || 10000,
    toggles: {
      text: true,
      candle: true,
      oi: true,
      nl: true,
      ns: true,
      vwap: true,
    },
  };
}

function buildCurrentTerminalTemplate() {
  return {
    provider: getRequestedProvider(),
    symbol: getRequestedSymbol(),
    duration_seconds: getRequestedDuration(),
    bar_mode: getRequestedBarMode(),
    range_ticks: getRequestedRangeTicks(),
    brick_length: getRequestedBrickLength(),
    toggles: { ...state.terminalToggles },
  };
}

function persistTerminalTemplate(template) {
  window.localStorage.setItem(TERMINAL_TEMPLATE_STORAGE_KEY, JSON.stringify(template));
}

function loadWatchlistSymbols() {
  const raw = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string" && item.trim()) : [];
  } catch {
    return [];
  }
}

function persistWatchlistSymbols(symbols) {
  state.watchlistSymbols = [...new Set(symbols.filter(Boolean))];
  window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(state.watchlistSymbols));
}

function syncWatchlistUi() {
  els.watchlistTabFavorites?.classList.toggle("is-active", state.watchlistMode === "favorites");
  els.watchlistTabAll?.classList.toggle("is-active", state.watchlistMode === "all");
  const current = getRequestedSymbol();
  const inWatchlist = state.watchlistSymbols.includes(current);
  if (els.watchlistAddCurrent) {
    els.watchlistAddCurrent.disabled = inWatchlist;
  }
  if (els.watchlistRemoveCurrent) {
    els.watchlistRemoveCurrent.disabled = !inWatchlist;
  }
}

function loadSavedTerminalTemplate() {
  const raw = window.localStorage.getItem(TERMINAL_TEMPLATE_STORAGE_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function syncToolbarToggles() {
  if (els.toggleText) els.toggleText.checked = Boolean(state.terminalToggles.text);
  if (els.toggleCandle) els.toggleCandle.checked = Boolean(state.terminalToggles.candle);
  if (els.toggleOi) els.toggleOi.checked = Boolean(state.terminalToggles.oi);
  if (els.toggleNl) els.toggleNl.checked = Boolean(state.terminalToggles.nl);
  if (els.toggleNs) els.toggleNs.checked = Boolean(state.terminalToggles.ns);
  if (els.toggleVwap) els.toggleVwap.checked = Boolean(state.terminalToggles.vwap);
}

async function applyTerminalTemplate(template) {
  const nextTemplate = template || buildDefaultTerminalTemplate();
  const availableProviders = new Set(state.config?.providers || [state.config?.provider].filter(Boolean));
  const nextProvider = availableProviders.has(String(nextTemplate.provider || ""))
    ? String(nextTemplate.provider)
    : state.config.provider;
  state.terminalToggles = {
    ...state.terminalToggles,
    ...(nextTemplate.toggles || {}),
  };
  syncToolbarToggles();

  if (els.toolbarProvider) els.toolbarProvider.value = nextProvider;
  if (els.providerSelect) els.providerSelect.value = nextProvider;
  if (els.toolbarSymbol) els.toolbarSymbol.value = String(nextTemplate.symbol || state.config.symbol);
  if (els.symbolSelect) els.symbolSelect.value = String(nextTemplate.symbol || state.config.symbol);
  if (els.toolbarDuration) els.toolbarDuration.value = String(nextTemplate.duration_seconds || state.config.duration_seconds);
  if (els.durationSelect) els.durationSelect.value = String(nextTemplate.duration_seconds || state.config.duration_seconds);
  if (els.toolbarBarMode) els.toolbarBarMode.value = String(nextTemplate.bar_mode || state.config.bar_mode);
  if (els.barModeSelect) els.barModeSelect.value = String(nextTemplate.bar_mode || state.config.bar_mode);
  if (els.toolbarRangeTicks) els.toolbarRangeTicks.value = String(nextTemplate.range_ticks || state.config.range_ticks || 10);
  if (els.rangeTicksInput) els.rangeTicksInput.value = String(nextTemplate.range_ticks || state.config.range_ticks || 10);
  if (els.toolbarBrickLength) els.toolbarBrickLength.value = String(nextTemplate.brick_length || state.config.brick_length || 10000);
  if (els.brickLengthInput) els.brickLengthInput.value = String(nextTemplate.brick_length || state.config.brick_length || 10000);

  syncBarModeControls(getRequestedBarMode());
  await refreshConfig(getRequestedProvider());
  await refreshSnapshot();
}

function indicatorParamValue(indicatorId, param) {
  const currentValue = state.indicatorParams[indicatorId]?.[param.key];
  return currentValue ?? param.default;
}

function createIndicatorParamInput(indicatorId, param, enabled) {
  let input;
  const currentValue = indicatorParamValue(indicatorId, param);

  if (param.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(currentValue);
  } else if (Array.isArray(param.options) && param.options.length > 0) {
    input = document.createElement("select");
    param.options.forEach((optionValue) => {
      const option = document.createElement("option");
      option.value = String(optionValue);
      option.textContent = String(optionValue);
      option.selected = String(currentValue) === String(optionValue);
      input.append(option);
    });
  } else if (param.type === "int" || param.type === "float") {
    input = document.createElement("input");
    input.type = "number";
    input.value = currentValue;
    input.step = param.step ?? "any";
    if (param.min !== undefined) {
      input.min = param.min;
    }
    if (param.max !== undefined) {
      input.max = param.max;
    }
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = currentValue;
  }

  input.dataset.indicatorId = indicatorId;
  input.dataset.paramKey = param.key;
  input.disabled = !enabled;
  input.addEventListener("change", async (event) => {
    const nextValue = param.type === "bool" ? event.target.checked : event.target.value;
    updateIndicatorParamState(indicatorId, param.key, nextValue);
    if (isIndicatorEnabled(indicatorId)) {
      await refreshSnapshot();
    }
  });
  return input;
}

function formatDurationLabel(seconds) {
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)} 分钟`;
  }
  if (seconds < 86400) {
    return `${Math.round(seconds / 3600)} 小时`;
  }
  return `${Math.round(seconds / 86400)} 天`;
}

function formatBarModeLabel(barMode, durationSeconds, rangeTicks) {
  if (barMode === "tick") {
    return "Tick 图";
  }
  if (barMode === "range") {
    return `${rangeTicks} Tick Range`;
  }
  if (barMode === "renko") {
    return `${rangeTicks} Tick Renko`;
  }
  return formatDurationLabel(durationSeconds);
}

function syncMarketHeader(symbolLabel, durationSeconds, barMode, rangeTicks) {
  const [primaryLabel, secondaryLabel] = String(symbolLabel || "").split("·").map((item) => item.trim());
  const mainLabel = primaryLabel || symbolLabel;
  els.title.textContent = `${mainLabel} 图表工作台`;
  els.symbol.textContent = mainLabel;
  els.duration.textContent = formatBarModeLabel(barMode, durationSeconds, rangeTicks);
  if (els.metaContract) {
    els.metaContract.textContent = secondaryLabel ? `${mainLabel} · ${secondaryLabel}` : mainLabel;
  }
}

function formatDetailValue(value, fallback = "--") {
  if (value === null || value === undefined) {
    return fallback;
  }
  const text = String(value).trim();
  return text ? text : fallback;
}

function formatNumberValue(value, digits = null) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "--";
  }
  if (digits === null) {
    return number.toLocaleString("zh-CN");
  }
  return number.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function renderProviderMeta(payload) {
  const provider = payload.provider || state.activeProvider || state.config?.provider || "";
  els.provider.textContent = provider || "--";
  els.providerHint.textContent = payload.provider_hint || "当前数据源暂无额外说明。";

  const detail = payload.contract_detail || {};
  const hasContractDetail = Object.keys(detail).length > 0;

  els.contractDetailCard.hidden = !hasContractDetail;
  els.detailFirstTick.textContent = formatDetailValue(detail.exchange_id);
  els.detailLastTick.textContent = formatDetailValue(detail.product_id);
  els.detailTickCount.textContent = formatDetailValue(detail.name);
  els.detailPriceTick.textContent = formatNumberValue(detail.price_tick, 4);
  els.detailContractMonth.textContent = formatDetailValue(detail.symbol);
  els.detailVolumeMultiple.textContent = formatNumberValue(detail.volume_multiple, 0);
}

function buildDurationOptions(options, activeValue) {
  els.durationSelect.innerHTML = "";
  if (els.toolbarDuration) {
    els.toolbarDuration.innerHTML = "";
  }
  options.forEach((seconds) => {
    const option = document.createElement("option");
    option.value = String(seconds);
    option.textContent = formatDurationLabel(seconds);
    option.selected = seconds === activeValue;
    els.durationSelect.append(option);
    if (els.toolbarDuration) {
      const clone = option.cloneNode(true);
      els.toolbarDuration.append(clone);
    }
  });
}

function buildProviderOptions(options, activeValue) {
  els.providerSelect.innerHTML = "";
  if (els.toolbarProvider) {
    els.toolbarProvider.innerHTML = "";
  }
  options.forEach((providerId) => {
    const option = document.createElement("option");
    option.value = providerId;
    option.textContent = providerId;
    option.selected = providerId === activeValue;
    els.providerSelect.append(option);
    if (els.toolbarProvider) {
      const clone = option.cloneNode(true);
      els.toolbarProvider.append(clone);
    }
  });
}

function buildBarModeOptions(options, activeValue) {
  els.barModeSelect.innerHTML = "";
  if (els.toolbarBarMode) {
    els.toolbarBarMode.innerHTML = "";
  }
  options.forEach((mode) => {
    const option = document.createElement("option");
    option.value = mode.id;
    option.textContent = mode.label;
    option.selected = mode.id === activeValue;
    els.barModeSelect.append(option);
    if (els.toolbarBarMode) {
      const clone = option.cloneNode(true);
      els.toolbarBarMode.append(clone);
    }
  });
}

function buildContractOptions(contracts, activeSymbol) {
  els.symbolSelect.innerHTML = "";
  if (els.toolbarSymbol) {
    els.toolbarSymbol.innerHTML = "";
  }
  let normalizedContracts = contracts.length
    ? contracts
    : [{ symbol: activeSymbol, label: activeSymbol }];
  if (state.watchlistMode === "favorites") {
    normalizedContracts = normalizedContracts.filter((contract) => state.watchlistSymbols.includes(contract.symbol));
  }
  if (normalizedContracts.length === 0 && state.watchlistMode === "favorites") {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "自选为空";
    option.disabled = true;
    option.selected = true;
    els.symbolSelect.append(option);
    if (els.toolbarSymbol) {
      els.toolbarSymbol.append(option.cloneNode(true));
    }
    syncWatchlistUi();
    return;
  }
  normalizedContracts.forEach((contract) => {
    const option = document.createElement("option");
    option.value = contract.symbol;
    option.textContent = contract.label;
    option.selected = contract.symbol === activeSymbol;
    els.symbolSelect.append(option);
    if (els.toolbarSymbol) {
      const clone = option.cloneNode(true);
      els.toolbarSymbol.append(clone);
    }
  });
  if (!normalizedContracts.some((contract) => contract.symbol === activeSymbol) && normalizedContracts[0]?.symbol) {
    els.symbolSelect.value = normalizedContracts[0].symbol;
    if (els.toolbarSymbol) {
      els.toolbarSymbol.value = normalizedContracts[0].symbol;
    }
  }
  syncWatchlistUi();
}

function getRequestedSymbol() {
  return els.symbolSelect.value || state.activeSymbol || state.config.symbol;
}

function getRequestedProvider() {
  return els.providerSelect.value || state.config.provider;
}

function getRequestedDuration() {
  return Number(els.durationSelect.value || state.config.duration_seconds);
}

function getRequestedBarMode() {
  return els.barModeSelect.value || state.config.bar_mode || "time";
}

function getRequestedRangeTicks() {
  const value = Number(els.rangeTicksInput.value || state.config.range_ticks || 10);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 10;
}

function getRequestedBrickLength() {
  const value = Number(els.brickLengthInput.value || state.config.brick_length || 10000);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 10000;
}

function defaultTicksForBarMode(barMode) {
  if (barMode === "renko") {
    return RENKO_DEFAULT_TICKS;
  }
  if (barMode === "range") {
    return RANGE_DEFAULT_TICKS;
  }
  return state.config?.range_ticks || RANGE_DEFAULT_TICKS;
}

function syncBarModeControls(barMode) {
  const usesDuration = barMode === "time";
  const usesTicks = barMode === "range" || barMode === "renko";
  const usesHistoryLength = barMode === "tick" || barMode === "range" || barMode === "renko";
  els.durationSelect.disabled = !usesDuration;
  els.rangeTicksInput.disabled = !usesTicks;
  els.brickLengthInput.disabled = !usesHistoryLength;
  if (barMode === "renko") {
    els.barSizeLabel.textContent = "Renko Tick";
    els.historySizeLabel.textContent = "砖图根数";
  } else if (barMode === "range") {
    els.barSizeLabel.textContent = "Range Tick";
    els.historySizeLabel.textContent = "砖图根数";
  } else if (barMode === "tick") {
    els.barSizeLabel.textContent = "价格 Tick";
    els.historySizeLabel.textContent = "Tick 根数";
  } else {
    els.barSizeLabel.textContent = "价格 Tick";
    els.historySizeLabel.textContent = "显示根数";
  }
}

function sanitizePricePaneIndicators(snapshot) {
  if (snapshot.bar_mode !== "time" || snapshot.candles.length === 0) {
    return snapshot;
  }

  const lows = snapshot.candles.map((item) => item.low).filter((value) => Number.isFinite(value));
  const highs = snapshot.candles.map((item) => item.high).filter((value) => Number.isFinite(value));
  if (lows.length === 0 || highs.length === 0) {
    return snapshot;
  }

  const candleMin = Math.min(...lows);
  const candleMax = Math.max(...highs);
  const lowerBound = candleMin > 0 ? candleMin * 0.5 : candleMin - Math.abs(candleMax - candleMin) * 2;
  const upperBound = candleMax > 0 ? candleMax * 1.5 : candleMax + Math.abs(candleMax - candleMin) * 2;

  return {
    ...snapshot,
    indicators: snapshot.indicators.map((indicator) => {
      if (indicator.pane !== "price") {
        return indicator;
      }
      return {
        ...indicator,
        series: indicator.series.map((series) => ({
          ...series,
          data: series.data.map((point) => {
            if (typeof point?.value !== "number") {
              return point;
            }
            if (point.value < lowerBound || point.value > upperBound) {
              return { ...point, value: null };
            }
            return point;
          }),
        })),
      };
    }),
  };
}

function applyIndicatorBarColors(snapshot) {
  if (!state.terminalToggles.candle || !Array.isArray(snapshot.candles) || snapshot.candles.length === 0) {
    return snapshot;
  }
  const colorByTime = new Map();
  (snapshot.indicators || []).forEach((indicator) => {
    (indicator.series || []).forEach((series) => {
      (series.options?.barColors || []).forEach((item) => {
        if (Number.isFinite(Number(item?.time)) && item?.color) {
          colorByTime.set(Number(item.time), item.color);
        }
      });
    });
  });
  if (colorByTime.size === 0) {
    return snapshot;
  }
  return {
    ...snapshot,
    candles: snapshot.candles.map((candle) => {
      const color = colorByTime.get(Number(candle.time));
      if (!color) {
        return candle;
      }
      return {
        ...candle,
        color,
        borderColor: color,
        wickColor: color,
      };
    }),
  };
}

function buildIndicatorSelector(indicators, defaults) {
  els.indicatorForm.innerHTML = "";
  indicators.forEach((indicator) => {
    const label = document.createElement("label");
    label.className = "indicator-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = indicator.id;
    checkbox.dataset.role = "indicator-toggle";
    checkbox.checked = defaults.includes(indicator.id);
    checkbox.addEventListener("change", async () => {
      state.selectedIndicators = getIndicatorIds();
      toggleParamInputs(indicator.id, checkbox.checked);
      try {
        const snapshot = await fetchSnapshotPayload();
        rebuildCharts();
        applySnapshot(snapshot);
        syncAutoRefresh(snapshot.refresh_ms ?? state.config?.refresh_ms ?? 0);
      } catch (error) {
        els.error.textContent = error.message;
      }
    });

    const content = document.createElement("div");
    content.className = "indicator-body";
    content.innerHTML = `<div><strong>${indicator.name}</strong><span>${indicator.description}</span></div>`;

    const paramsWrap = document.createElement("div");
    paramsWrap.className = "indicator-params";
    paramsWrap.dataset.indicatorId = indicator.id;

    (indicator.params || []).forEach((param) => {
      const field = document.createElement("label");
      field.className = "indicator-param";

      const title = document.createElement("small");
      title.textContent = param.label;

      const input = createIndicatorParamInput(indicator.id, param, checkbox.checked);

      field.append(title, input);
      paramsWrap.append(field);
    });

    if ((indicator.params || []).length > 0) {
      content.append(paramsWrap);
    }

    label.append(checkbox, content);
    els.indicatorForm.append(label);
  });
}

function toggleParamInputs(indicatorId, enabled) {
  els.indicatorForm.querySelectorAll(`[data-indicator-id="${indicatorId}"][data-param-key]`).forEach((input) => {
    input.disabled = !enabled;
  });
}

function paneLayoutFor(indicators) {
  const panes = ["price"];
  if (indicators.some((item) => item.pane === "indicator")) {
    panes.push(VOLUME_PANE_ID);
  }
  indicators.forEach((item) => {
    if (item.pane === "indicator") {
      panes.push(item.id);
    }
  });
  return panes;
}

function syncRange(sourceChart, targetChart) {
  sourceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range && !state.isAdjustingRange) {
      targetChart.timeScale().setVisibleLogicalRange(range);
    }
  });
}

function seriesValueAtPoint(point) {
  if (!point) {
    return null;
  }
  if (typeof point.value === "number") {
    return point.value;
  }
  if (typeof point.close === "number") {
    return point.close;
  }
  if (typeof point.high === "number") {
    return point.high;
  }
  if (typeof point.open === "number") {
    return point.open;
  }
  return null;
}

function findSeriesPointAtTime(seriesKey, time) {
  const points = state.seriesDataByKey.get(seriesKey) || [];
  const index = state.seriesIndexByKey.get(seriesKey)?.get(String(time));
  return index === undefined ? null : points[index] || null;
}

function normalizeSeriesTime(time) {
  const numeric = Number(time);
  return Number.isFinite(numeric) ? numeric : time;
}

function buildSeriesIndex(data) {
  const index = new Map();
  (data || []).forEach((point, pointIndex) => {
    if (point?.time !== undefined && point?.time !== null) {
      index.set(String(point.time), pointIndex);
    }
  });
  return index;
}

function seriesPointsEqual(left, right) {
  if (left === right) {
    return true;
  }
  if (!left || !right) {
    return false;
  }
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => left[key] === right[key]);
}

function updateSeriesFromDelta(seriesKey, series, points, maxLength) {
  if (!Array.isArray(points) || points.length === 0) {
    return true;
  }
  let data = state.seriesDataByKey.get(seriesKey);
  if (!Array.isArray(data)) {
    data = [];
  }
  let index = state.seriesIndexByKey.get(seriesKey);
  if (!index) {
    index = buildSeriesIndex(data);
  }
  const originalLastIndex = data.length - 1;
  const updates = [];
  let requiresSetData = typeof series?.update !== "function";

  points.forEach((point) => {
    if (!point || point.time === undefined || point.time === null) {
      return;
    }
    const nextPoint = { ...point, time: normalizeSeriesTime(point.time) };
    const timeKey = String(nextPoint.time);
    const existingIndex = index.get(timeKey);
    if (existingIndex !== undefined) {
      if (seriesPointsEqual(data[existingIndex], nextPoint)) {
        return;
      }
      data[existingIndex] = nextPoint;
      if (existingIndex < originalLastIndex) {
        requiresSetData = true;
      } else {
        updates.push(nextPoint);
      }
      return;
    }

    const lastTime = Number(data[data.length - 1]?.time);
    const pointTime = Number(nextPoint.time);
    if (data.length > 0 && Number.isFinite(lastTime) && Number.isFinite(pointTime) && pointTime < lastTime) {
      data.push(nextPoint);
      data.sort((left, right) => Number(left?.time || 0) - Number(right?.time || 0));
      index = buildSeriesIndex(data);
      requiresSetData = true;
      return;
    }
    index.set(timeKey, data.length);
    data.push(nextPoint);
    updates.push(nextPoint);
  });

  const limit = Number(maxLength);
  if (Number.isFinite(limit) && limit > 0 && data.length > limit) {
    data.splice(0, data.length - limit);
    index = buildSeriesIndex(data);
    requiresSetData = true;
  }

  if (requiresSetData && typeof series?.setData === "function") {
    series.setData(data);
  } else if (typeof series?.update === "function") {
    updates.forEach((point) => series.update(point));
  }
  state.seriesDataByKey.set(seriesKey, data);
  state.seriesIndexByKey.set(seriesKey, index);
  return true;
}

function setSeriesData(seriesKey, series, data) {
  if (typeof series?.setData === "function") {
    series.setData(data);
  }
  state.seriesDataByKey.set(seriesKey, data);
  state.seriesIndexByKey.set(seriesKey, buildSeriesIndex(data));
}

function removeSeriesByKey(seriesKey) {
  const primitive = state.bandPrimitiveByKey.get(seriesKey);
  const series = state.seriesByKey.get(seriesKey);
  if (primitive && series?.detachPrimitive) {
    series.detachPrimitive(primitive);
  }
  state.bandPrimitiveByKey.delete(seriesKey);

  const chart = state.seriesChartByKey.get(seriesKey);
  if (chart && series && typeof chart.removeSeries === "function" && typeof series.applyOptions === "function") {
    chart.removeSeries(series);
  }
  if (series && typeof series.destroy === "function") {
    series.destroy();
  }

  state.seriesByKey.delete(seriesKey);
  state.seriesChartByKey.delete(seriesKey);
  state.seriesDataByKey.delete(seriesKey);
  state.seriesIndexByKey.delete(seriesKey);
  state.primarySeriesKeyByPane.forEach((value, key) => {
    if (value === seriesKey) {
      state.primarySeriesKeyByPane.delete(key);
    }
  });
}

function syncBandPrimitive(primaryKey, secondaryKey, fillColor) {
  const primarySeries = state.seriesByKey.get(primaryKey);
  const secondarySeries = state.seriesByKey.get(secondaryKey);
  if (!primarySeries || !secondarySeries || typeof primarySeries.attachPrimitive !== "function") {
    const existing = state.bandPrimitiveByKey.get(primaryKey);
    if (existing && primarySeries?.detachPrimitive) {
      primarySeries.detachPrimitive(existing);
      state.bandPrimitiveByKey.delete(primaryKey);
    }
    return;
  }

  let primitive = state.bandPrimitiveByKey.get(primaryKey);
  if (!primitive) {
    primitive = new LineBandPrimitive(fillColor);
    primarySeries.attachPrimitive(primitive);
    state.bandPrimitiveByKey.set(primaryKey, primitive);
  }

  primitive.setFillColor(fillColor);
  primitive.setSecondarySeries(secondarySeries);
  primitive.setData(
    state.seriesDataByKey.get(primaryKey) || [],
    state.seriesDataByKey.get(secondaryKey) || []
  );
}

function primarySeriesKeyForPane(paneId) {
  if (paneId === "price") {
    return "candles";
  }
  if (paneId === VOLUME_PANE_ID) {
    return "volume";
  }
  return state.primarySeriesKeyByPane.get(paneId) || null;
}

function syncCrosshair(sourcePaneId, param) {
  if (state.isSyncingCrosshair) {
    return;
  }

  const time = param?.time;
  if (time === undefined) {
    els.cursorTime.textContent = "--";
    state.isSyncingCrosshair = true;
    state.charts.forEach((entry) => {
      entry.chart.clearCrosshairPosition();
    });
    state.isSyncingCrosshair = false;
    return;
  }

  els.cursorTime.textContent = formatCrosshairTime(time);
  state.isSyncingCrosshair = true;
  state.charts.forEach((entry) => {
    if (entry.paneId === sourcePaneId) {
      return;
    }

    const seriesKey = primarySeriesKeyForPane(entry.paneId);
    if (!seriesKey) {
      entry.chart.clearCrosshairPosition();
      return;
    }

    const series = state.seriesByKey.get(seriesKey);
    const point = findSeriesPointAtTime(seriesKey, time);
    const value = seriesValueAtPoint(point);
    if (!series || value === null) {
      entry.chart.clearCrosshairPosition();
      return;
    }

    entry.chart.setCrosshairPosition(value, time, series);
  });
  state.isSyncingCrosshair = false;
}

function focusRecentBars(chart, barCount) {
  const visibleBars = Math.min(DEFAULT_VISIBLE_BARS, barCount);
  chart.timeScale().setVisibleLogicalRange({
    from: Math.max(0, barCount - visibleBars),
    to: barCount - 1 + RANGE_RIGHT_PADDING_BARS,
  });
}

function firstDefinedPointIndex(points) {
  return points.findIndex((point) => point && point.value !== null && point.value !== undefined);
}

function indicatorReadyIndex(snapshot) {
  const seriesIndices = snapshot.indicators.flatMap((indicator) =>
    indicator.series
      .filter((series) => Array.isArray(series.data))
      .map((series) => firstDefinedPointIndex(series.data))
      .filter((index) => index >= 0)
  );

  if (seriesIndices.length === 0) {
    return 0;
  }
  return Math.max(...seriesIndices);
}

function trimSnapshotForDisplay(snapshot) {
  const readyIndex = indicatorReadyIndex(snapshot);
  const maxTrim = Math.max(snapshot.candles.length - MIN_VISIBLE_DATA_BARS, 0);
  const trimCount = Math.min(Math.max(readyIndex, 0), maxTrim);
  if (trimCount <= 0) {
    return snapshot;
  }

  const timeLabels = Object.fromEntries(
    snapshot.candles.slice(trimCount).map((candle) => [String(candle.time), snapshot.time_labels?.[String(candle.time)] || ""])
  );

  return {
    ...snapshot,
    time_labels: timeLabels,
    candles: snapshot.candles.slice(trimCount),
    volume: snapshot.volume.slice(trimCount),
    indicators: snapshot.indicators.map((indicator) => ({
      ...indicator,
      series: indicator.series.map((series) => ({
        ...series,
        data: series.data.slice(trimCount),
      })),
    })),
  };
}

function focusComputedBars(chart, snapshot) {
  const barCount = snapshot.candles.length;
  if (barCount === 0) {
    return;
  }

  const readyIndex = indicatorReadyIndex(snapshot);
  const visibleBars = Math.min(DEFAULT_VISIBLE_BARS, Math.max(1, barCount - readyIndex));
  const from = Math.max(readyIndex, barCount - visibleBars);
  chart.timeScale().setVisibleLogicalRange({
    from,
    to: barCount - 1 + RANGE_RIGHT_PADDING_BARS,
  });
}

function snapshotViewportSignature(snapshot) {
  return [
    snapshot.provider || "",
    snapshot.symbol || "",
    Number(snapshot.duration_seconds || 0),
    snapshot.bar_mode || "time",
    Number(snapshot.range_ticks || 0),
    Number(snapshot.brick_length || 0),
  ].join("|");
}

function resetChartViewport(snapshot) {
  state.charts.forEach((entry) => {
    entry.chart.priceScale("right").applyOptions({ autoScale: true });
  });
  if (snapshot.indicators.length > 0) {
    focusComputedBars(state.charts[0].chart, snapshot);
  } else {
    focusRecentBars(state.charts[0].chart, snapshot.candles.length);
  }
}

function paneHeights(panes) {
  const total = panes.length;
  const hasVolumePane = panes.includes(VOLUME_PANE_ID);

  if (!hasVolumePane) {
    if (total <= 1) {
      return [100];
    }
    if (total === 2) {
      return [80, 20];
    }
    if (total === 3) {
      return [72, 14, 14];
    }
    if (total === 4) {
      return [64, 12, 12, 12];
    }

    const pricePaneHeight = 58;
    const secondaryPaneHeight = (100 - pricePaneHeight) / (total - 1);
    return [pricePaneHeight, ...Array.from({ length: total - 1 }, () => secondaryPaneHeight)];
  }

  const indicatorCount = total - 2;
  if (indicatorCount <= 0) {
    return [82, 18];
  }
  if (indicatorCount === 1) {
    return [46, 14, 40];
  }
  if (indicatorCount === 2) {
    return [44, 12, 22, 22];
  }
  if (indicatorCount === 3) {
    return [42, 10, 16, 16, 16];
  }

  const pricePaneHeight = 46;
  const volumePaneHeight = 10;
  const secondaryPaneHeight = (100 - pricePaneHeight - volumePaneHeight) / indicatorCount;
  return [
    pricePaneHeight,
    volumePaneHeight,
    ...Array.from({ length: indicatorCount }, () => secondaryPaneHeight),
  ];
}

function volumeOverlayScaleMargins(totalPanes) {
  if (totalPanes <= 1) {
    return { top: 0.82, bottom: 0.02 };
  }
  if (totalPanes === 2) {
    return { top: 0.8, bottom: 0.02 };
  }
  if (totalPanes === 3) {
    return { top: 0.82, bottom: 0.02 };
  }
  return { top: 0.84, bottom: 0.02 };
}

function currentRequestedDataLength() {
  return state.requestedDataLength || state.config?.data_length || 800;
}

function rebuildCharts() {
  state.charts.forEach((entry) => entry.chart.remove());
  state.charts = [];
  state.seriesByKey = new Map();
  state.seriesChartByKey = new Map();
  state.seriesDataByKey = new Map();
  state.seriesIndexByKey = new Map();
  state.primarySeriesKeyByPane = new Map();
  state.bandPrimitiveByKey = new Map();
  state.currentPriceLine = null;
  state.hasFitted = false;
  els.chartStack.innerHTML = "";

  const configuredIndicators = state.config.indicators.filter((item) => state.selectedIndicators.includes(item.id));
  const activeIndicators = [
    ...configuredIndicators,
    ...state.runtimeIndicators,
  ];
  const panes = paneLayoutFor(activeIndicators);
  const heights = paneHeights(panes);
  els.chartStack.style.gap = panes.length <= 1 ? "8px" : panes.length === 2 ? "5px" : "3px";

  panes.forEach((paneId, index) => {
    const pane = document.createElement("div");
    pane.className = "chart-pane";
    pane.style.flexBasis = `${heights[index]}%`;
    pane.style.height = `${heights[index]}%`;
    pane.style.minHeight =
      paneId === "price"
        ? "180px"
        : paneId === VOLUME_PANE_ID
          ? "56px"
          : "96px";
    els.chartStack.appendChild(pane);

    const paneLabels = paneLabelConfig(paneId);
    let labelOverlay = null;
    let labelElements = [];
    if (paneLabels.length > 0) {
      const overlay = document.createElement("div");
      overlay.className = "pane-label-overlay";
      paneLabels.forEach((item) => {
        const label = document.createElement("div");
        label.className = "pane-label-tag";
        label.textContent = item.text;
        overlay.appendChild(label);
        labelElements.push(label);
      });
      pane.appendChild(overlay);
      labelOverlay = overlay;
    }

    const chart = LightweightCharts.createChart(pane, {
      width: pane.clientWidth || 800,
      height: pane.clientHeight || 300,
      ...chartTheme,
    });

    state.charts.push({ paneId, container: pane, chart, labelOverlay, labelElements, labelConfig: paneLabels });
    chart.subscribeCrosshairMove((param) => {
      syncCrosshair(paneId, param);
    });
    if (paneId === "price") {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      });
    }
  });

  for (let i = 0; i < state.charts.length - 1; i += 1) {
    syncRange(state.charts[i].chart, state.charts[i + 1].chart);
    syncRange(state.charts[i + 1].chart, state.charts[i].chart);
  }

  const priceChart = state.charts[0].chart;
  const volumePaneEntry = state.charts.find((entry) => entry.paneId === VOLUME_PANE_ID);
  const volumeChart = volumePaneEntry?.chart || priceChart;
  const candleSeries = priceChart.addCandlestickSeries({
    upColor: "#6eff77",
    downColor: "#ff335f",
    borderVisible: false,
    wickUpColor: "#6eff77",
    wickDownColor: "#ff335f",
    priceLineVisible: false,
    autoscaleInfoProvider: (original) => {
      const autoscaleInfo = original();
      if (!autoscaleInfo || !autoscaleInfo.priceRange) {
        return autoscaleInfo;
      }

      const minValue = autoscaleInfo.priceRange.minValue;
      const maxValue = autoscaleInfo.priceRange.maxValue;
      const range = Math.max(maxValue - minValue, Math.abs(maxValue) * 0.01, 1e-6);
      const bottomPadding = volumePaneEntry
        ? PRICE_RANGE_BOTTOM_PADDING_WITH_VOLUME_PANE
        : PRICE_RANGE_BOTTOM_PADDING;
      return {
        ...autoscaleInfo,
        priceRange: {
          minValue: minValue - range * bottomPadding,
          maxValue: maxValue + range * PRICE_RANGE_TOP_PADDING,
        },
      };
    },
  });
  let volumeSeries;
  if (volumePaneEntry) {
    volumeSeries = volumeChart.addHistogramSeries({
      priceFormat: { type: "volume" },
    });
    volumeChart.priceScale("right").applyOptions({
      autoScale: true,
      minimumWidth: RIGHT_PRICE_SCALE_MIN_WIDTH,
      scaleMargins: { top: 0.08, bottom: 0.12 },
    });
  } else {
    volumeSeries = priceChart.addHistogramSeries({
      priceScaleId: "",
      priceFormat: { type: "volume" },
    });
    priceChart.priceScale("").applyOptions({
      scaleMargins: volumeOverlayScaleMargins(panes.length),
    });
  }

  state.charts.forEach((entry, index) => {
    const isLastPane = index === state.charts.length - 1;
    const isVolumePane = entry.paneId === VOLUME_PANE_ID;
    entry.chart.timeScale().applyOptions({
      visible: isLastPane,
    });

    if (entry.paneId !== "price" && !isVolumePane) {
      entry.chart.priceScale("right").applyOptions({
        autoScale: true,
        minimumWidth: RIGHT_PRICE_SCALE_MIN_WIDTH,
        scaleMargins: { top: 0.18, bottom: 0.18 },
      });
    }
  });

  state.seriesByKey.set("candles", candleSeries);
  state.seriesByKey.set("volume", volumeSeries);
  state.seriesChartByKey.set("candles", priceChart);
  state.seriesChartByKey.set("volume", volumeChart);
}

function createSeries(paneEntry, definition) {
  const chart = paneEntry.chart;
  const { fillToSeriesId, fillColor, markers, candleMarkers, barColors, ...renderOptions } = definition.options || {};
  switch (definition.series_type) {
    case "line":
      return chart.addLineSeries(renderOptions);
    case "histogram":
      return chart.addHistogramSeries(renderOptions);
    case "area":
      return chart.addAreaSeries(renderOptions);
    default:
      throw new Error(`暂不支持的序列类型: ${definition.series_type}`);
  }
}

function indicatorPaneId(indicator) {
  return indicator.pane === "price" ? "price" : indicator.id;
}

function formatCrosshairTime(time) {
  const resolved = resolveDisplayTime(time);
  if (resolved) {
    return resolved;
  }
  if (state.activeBarMode === "time") {
    return "--";
  }
  if (typeof time === "number") {
    if (Math.abs(time) < 1e9) {
      return new Date(time * 1000 * 1000).toLocaleString("zh-CN", {
        hour12: false,
      });
    }
    return new Date(time * 1000).toLocaleString("zh-CN", {
      hour12: false,
    });
  }
  if (time && typeof time === "object" && "year" in time) {
    const month = String(time.month).padStart(2, "0");
    const day = String(time.day).padStart(2, "0");
    return `${time.year}-${month}-${day}`;
  }
  return "--";
}

function deltaContextMatches(delta) {
  if (!delta || typeof delta !== "object") {
    return false;
  }
  if (
    delta.provider !== state.activeProvider ||
    delta.symbol !== state.activeSymbol ||
    delta.duration_seconds !== state.activeDurationSeconds ||
    (delta.bar_mode || "time") !== state.activeBarMode ||
    (delta.range_ticks || state.activeRangeTicks || 10) !== state.activeRangeTicks ||
    (delta.brick_length || state.activeBrickLength || 10000) !== state.activeBrickLength
  ) {
    return false;
  }
  const baseVersion = Number(delta.base_version);
  if (Number.isFinite(baseVersion) && state.streamVersion !== null && baseVersion !== Number(state.streamVersion)) {
    return false;
  }
  return true;
}

function updateTimeIndexFromDelta(candles, timeLabels, barMode) {
  Object.entries(timeLabels || {}).forEach(([time, label]) => {
    state.timeLabels.set(String(time), label);
  });
  (candles || []).forEach((candle) => {
    const syntheticTime = Number(candle?.time);
    if (!Number.isFinite(syntheticTime)) {
      return;
    }
    const label = timeLabels?.[String(syntheticTime)] || state.timeLabels.get(String(syntheticTime)) || "";
    const actualTimeMs = (barMode || state.activeBarMode) === "time"
      ? syntheticTime * 1000
      : Date.parse(String(label).replace(" ", "T"));
    if (!Number.isFinite(actualTimeMs)) {
      return;
    }
    state.wsActualToSyntheticTime.set(actualTimeMs, syntheticTime);
    state.wsSyntheticToActualTime.set(syntheticTime, actualTimeMs);
    state.wsMaxSyntheticTime = state.wsMaxSyntheticTime === null ? syntheticTime : Math.max(state.wsMaxSyntheticTime, syntheticTime);
    state.wsMaxActualTimeMs = state.wsMaxActualTimeMs === null ? actualTimeMs : Math.max(state.wsMaxActualTimeMs, actualTimeMs);
  });
}

function pruneTimeIndexToVisibleCandles() {
  const visibleTimes = new Set((state.seriesDataByKey.get("candles") || []).map((item) => String(item.time)));
  state.timeLabels.forEach((_label, time) => {
    if (!visibleTimes.has(String(time))) {
      state.timeLabels.delete(time);
    }
  });
  state.wsSyntheticToActualTime.forEach((actualTimeMs, syntheticTime) => {
    if (!visibleTimes.has(String(syntheticTime))) {
      state.wsSyntheticToActualTime.delete(syntheticTime);
      state.wsActualToSyntheticTime.delete(actualTimeMs);
    }
  });
}

function deltaBarColors(indicators) {
  const colors = new Map();
  (indicators || []).forEach((indicator) => {
    (indicator.series || []).forEach((series) => {
      (series.options?.barColors || []).forEach((item) => {
        if (Number.isFinite(Number(item?.time)) && item?.color) {
          colors.set(Number(item.time), item.color);
        }
      });
    });
  });
  return colors;
}

function applyDeltaCandleColors(candles, indicators) {
  if (!state.terminalToggles.candle) {
    return candles || [];
  }
  const colors = deltaBarColors(indicators);
  if (colors.size === 0) {
    return candles || [];
  }
  return (candles || []).map((candle) => {
    const color = colors.get(Number(candle.time));
    if (!color) {
      return candle;
    }
    return {
      ...candle,
      color,
      borderColor: color,
      wickColor: color,
    };
  });
}

function canApplyIndicatorDelta(indicators) {
  return (indicators || []).every((indicator) =>
    (indicator.series || []).every((seriesDefinition) => {
      const key = `indicator:${indicator.id}:${seriesDefinition.id}`;
      return state.seriesByKey.has(key);
    })
  );
}

function refreshBandPrimitivesFromDelta(indicators) {
  (indicators || []).forEach((indicator) => {
    (indicator.series || []).forEach((seriesDefinition) => {
      if (!seriesDefinition.options?.fillToSeriesId) {
        return;
      }
      syncBandPrimitive(
        `indicator:${indicator.id}:${seriesDefinition.id}`,
        `indicator:${indicator.id}:${seriesDefinition.options.fillToSeriesId}`,
        seriesDefinition.options.fillColor || "rgba(255, 152, 0, 0.16)"
      );
    });
  });
}

function applySnapshotDelta(delta) {
  if (!deltaContextMatches(delta)) {
    return false;
  }
  const candleSeries = state.seriesByKey.get("candles");
  const volumeSeries = state.seriesByKey.get("volume");
  if (!candleSeries || !volumeSeries || !canApplyIndicatorDelta(delta.indicators || [])) {
    return false;
  }

  const deltaVersion = Number(delta.version ?? delta.stream?.version);
  state.streamVersion = Number.isFinite(deltaVersion) ? deltaVersion : state.streamVersion;
  state.config.symbol = delta.symbol;
  state.config.provider = delta.provider;
  state.config.duration_seconds = delta.duration_seconds;
  state.config.bar_mode = delta.bar_mode || "time";
  state.config.range_ticks = delta.range_ticks || state.activeRangeTicks;
  state.config.brick_length = delta.brick_length || state.activeBrickLength;
  updateTimeIndexFromDelta(delta.candles || [], delta.time_labels || {}, delta.bar_mode);

  const maxLength = currentRequestedDataLength();
  updateSeriesFromDelta("candles", candleSeries, applyDeltaCandleColors(delta.candles || [], delta.indicators || []), maxLength);
  updateSeriesFromDelta("volume", volumeSeries, delta.volume || [], maxLength);
  pruneTimeIndexToVisibleCandles();

  (delta.indicators || []).forEach((indicator) => {
    (indicator.series || []).forEach((seriesDefinition) => {
      const key = `indicator:${indicator.id}:${seriesDefinition.id}`;
      const series = state.seriesByKey.get(key);
      if (typeof series?.setDefinitionOptions === "function") {
        series.setDefinitionOptions(seriesDefinition.options || {});
      }
      updateSeriesFromDelta(key, series, seriesDefinition.data || [], maxLength);
    });
  });
  refreshBandPrimitivesFromDelta(delta.indicators || []);

  const lastClose = Number(delta.last_close);
  if (Number.isFinite(lastClose)) {
    els.lastPrice.textContent = lastClose.toFixed(2);
    els.lastPrice.style.color = delta.last_color || "#089981";
    syncCurrentPriceLine(lastClose, delta.last_color || "#089981");
  }
  els.lastUpdate.textContent = delta.last_time || els.lastUpdate.textContent;
  if (els.metaStatus) {
    els.metaStatus.textContent = `Realtime ${state.activeProvider} · ${state.activeBarMode} · ${delta.last_time || "--"}`;
  }
  syncMarketHeader(
    delta.symbol_label || delta.symbol,
    delta.duration_seconds,
    state.activeBarMode,
    state.activeRangeTicks
  );
  updatePaneLabelPositions();
  return true;
}

function applySnapshot(snapshot) {
  els.error.textContent = "";
  const nextBarMode = snapshot.bar_mode || "time";
  const nextRangeTicks = snapshot.range_ticks || state.config.range_ticks || 10;
  const nextBrickLength = snapshot.brick_length || state.config.brick_length || 10000;
  const nextViewportSignature = snapshotViewportSignature({
    ...snapshot,
    bar_mode: nextBarMode,
    range_ticks: nextRangeTicks,
    brick_length: nextBrickLength,
  });
  const shouldRefit = nextViewportSignature !== state.renderedViewportSignature;
  const symbolChanged = Boolean(state.renderedSymbol) && snapshot.symbol !== state.renderedSymbol;

  state.activeSymbol = snapshot.symbol;
  state.activeProvider = snapshot.provider || state.config.provider;
  state.activeDurationSeconds = snapshot.duration_seconds;
  state.activeBarMode = nextBarMode;
  state.activeRangeTicks = nextRangeTicks;
  state.activeBrickLength = nextBrickLength;
  state.config.symbol = snapshot.symbol;
  state.config.provider = state.activeProvider;
  state.config.duration_seconds = snapshot.duration_seconds;
  state.config.bar_mode = nextBarMode;
  state.config.range_ticks = nextRangeTicks;
  state.config.brick_length = nextBrickLength;
  const snapshotVersion = Number(snapshot.stream?.version);
  state.streamVersion = Number.isFinite(snapshotVersion) ? snapshotVersion : null;
  state.timeLabels = new Map(Object.entries(snapshot.time_labels || {}));
  rebuildWsTimeIndex(snapshot);
  els.symbolSelect.value = snapshot.symbol;
  els.providerSelect.value = state.activeProvider;
  els.barModeSelect.value = state.activeBarMode;
  els.durationSelect.value = String(snapshot.duration_seconds);
  if (els.toolbarProvider) {
    els.toolbarProvider.value = state.activeProvider;
  }
  if (els.toolbarSymbol) {
    els.toolbarSymbol.value = snapshot.symbol;
  }
  if (els.toolbarDuration) {
    els.toolbarDuration.value = String(snapshot.duration_seconds);
  }
  if (els.toolbarBarMode) {
    els.toolbarBarMode.value = state.activeBarMode;
  }
  els.rangeTicksInput.value = String(state.activeRangeTicks);
  els.brickLengthInput.value = String(state.activeBrickLength);
  if (els.toolbarRangeTicks) {
    els.toolbarRangeTicks.value = String(state.activeRangeTicks);
  }
  if (els.toolbarBrickLength) {
    els.toolbarBrickLength.value = String(state.activeBrickLength);
  }
  syncBarModeControls(state.activeBarMode);
  syncMarketHeader(
    snapshot.symbol_label || snapshot.symbol,
    snapshot.duration_seconds,
    state.activeBarMode,
    state.activeRangeTicks
  );
  renderProviderMeta(snapshot);
  els.lastPrice.textContent = snapshot.last_close.toFixed(2);
  els.lastPrice.style.color = snapshot.last_color;
  els.lastUpdate.textContent = snapshot.last_time;
  if (els.metaStatus) {
    els.metaStatus.textContent = `Realtime ${state.activeProvider} · ${state.activeBarMode} · ${snapshot.last_time}`;
  }

  const sanitizedSnapshot = sanitizePricePaneIndicators(snapshot);
  const trimmedSnapshot = trimSnapshotForDisplay(sanitizedSnapshot);
  const displaySnapshot = applyIndicatorBarColors(trimmedSnapshot);
  const configuredIds = new Set(state.config.indicators.map((item) => item.id));
  state.runtimeIndicators = displaySnapshot.indicators.filter((item) => !configuredIds.has(item.id));
  const requiredPaneIds = new Set(paneLayoutFor([...state.config.indicators.filter((item) => state.selectedIndicators.includes(item.id)), ...state.runtimeIndicators]));
  const currentPaneIds = new Set(state.charts.map((item) => item.paneId));
  if (requiredPaneIds.size !== currentPaneIds.size || [...requiredPaneIds].some((paneId) => !currentPaneIds.has(paneId))) {
    rebuildCharts();
  }
  state.timeLabels = new Map(Object.entries(displaySnapshot.time_labels || {}));
  const previousCandleCount = (state.seriesDataByKey.get("candles") || []).length;
  const candleSeries = state.seriesByKey.get("candles");
  const volumeSeries = state.seriesByKey.get("volume");
  if (symbolChanged) {
    clearCurrentPriceLine();
  }
  setSeriesData("candles", candleSeries, displaySnapshot.candles);
  setSeriesData("volume", volumeSeries, displaySnapshot.volume);
  candleSeries?.applyOptions({
    upColor: state.terminalToggles.candle ? "#6eff77" : "rgba(0,0,0,0)",
    downColor: state.terminalToggles.candle ? "#ff335f" : "rgba(0,0,0,0)",
    wickUpColor: state.terminalToggles.candle ? "#6eff77" : "rgba(0,0,0,0)",
    wickDownColor: state.terminalToggles.candle ? "#ff335f" : "rgba(0,0,0,0)",
  });
  const activeBandPrimaryKeys = new Set();
  const candleMarkers = [];

  syncCurrentPriceLine(snapshot.last_close, snapshot.last_color);

  displaySnapshot.indicators.forEach((indicator) => {
    const activeSeriesKeys = new Set(["candles", "volume"]);
    const bandConfigs = [];
    const paneId = indicatorPaneId(indicator);
    const paneEntry = state.charts.find((item) => item.paneId === paneId);
    if (!paneEntry) {
      return;
    }

    indicator.series.forEach((seriesDefinition) => {
      const key = `indicator:${indicator.id}:${seriesDefinition.id}`;
      activeSeriesKeys.add(key);
      let series = state.seriesByKey.get(key);
      if (!series) {
        series = createSeries(paneEntry, seriesDefinition);
        state.seriesByKey.set(key, series);
        if (typeof series.applyOptions === "function") {
          state.seriesChartByKey.set(key, paneEntry.chart);
        }
        if (!state.primarySeriesKeyByPane.has(paneId)) {
          state.primarySeriesKeyByPane.set(paneId, key);
        }
      }
      if (typeof series.setDefinitionOptions === "function") {
        series.setDefinitionOptions(seriesDefinition.options || {});
      }
      setSeriesData(key, series, seriesDefinition.data);
      if (typeof series.setMarkers === "function") {
        series.setMarkers(seriesDefinition.options?.markers || []);
      }
      if (Array.isArray(seriesDefinition.options?.candleMarkers)) {
        candleMarkers.push(...seriesDefinition.options.candleMarkers);
      }

      if (seriesDefinition.options?.fillToSeriesId) {
        activeBandPrimaryKeys.add(key);
        bandConfigs.push({
          primaryKey: key,
          secondaryKey: `indicator:${indicator.id}:${seriesDefinition.options.fillToSeriesId}`,
          fillColor: seriesDefinition.options.fillColor || "rgba(255, 152, 0, 0.16)",
        });
      }
    });

    bandConfigs.forEach((config) => {
      syncBandPrimitive(config.primaryKey, config.secondaryKey, config.fillColor);
    });

    [...state.seriesByKey.keys()]
      .filter((key) => key.startsWith(`indicator:${indicator.id}:`) && !activeSeriesKeys.has(key))
      .forEach((key) => removeSeriesByKey(key));
  });

  if (typeof candleSeries?.setMarkers === "function") {
    candleSeries.setMarkers(candleMarkers.sort((left, right) => Number(left.time) - Number(right.time)));
  }

  [...state.bandPrimitiveByKey.keys()]
    .filter((key) => !activeBandPrimaryKeys.has(key))
    .forEach((key) => {
      const series = state.seriesByKey.get(key);
      const primitive = state.bandPrimitiveByKey.get(key);
      if (series?.detachPrimitive && primitive) {
        series.detachPrimitive(primitive);
      }
      state.bandPrimitiveByKey.delete(key);
    });

  if (shouldRefit) {
    state.hasFitted = false;
  }
  if (!state.hasFitted && state.charts.length > 0) {
    resetChartViewport(displaySnapshot);
    state.hasFitted = true;
  }
  state.renderedViewportSignature = nextViewportSignature;
  state.renderedSymbol = snapshot.symbol;

  updatePaneLabelPositions();
}

async function refreshSnapshot() {
  const requestId = ++state.snapshotRequestId;
  const requestedProvider = getRequestedProvider();
  const requestedSymbol = getRequestedSymbol();
  state.snapshotRefreshInFlight = true;
  try {
    const snapshot = await fetchSnapshotPayload();
    if (requestId !== state.snapshotRequestId) {
      return;
    }
    if (
      snapshot.provider !== requestedProvider ||
      snapshot.symbol !== requestedSymbol
    ) {
      return;
    }
    applySnapshot(snapshot);
    syncAutoRefresh(snapshot.refresh_ms ?? state.config?.refresh_ms ?? 0);
    syncRealtimeTransport();
  } finally {
    if (requestId === state.snapshotRequestId) {
      state.snapshotRefreshInFlight = false;
    }
  }
}

async function fetchSnapshotPayload() {
  const params = buildSnapshotParams();
  const query = params.toString();
  return fetchJson(`/api/snapshot${query ? `?${query}` : ""}`);
}

function buildSnapshotParams() {
  const params = new URLSearchParams();
  params.set("provider", getRequestedProvider());
  params.set("symbol", getRequestedSymbol());
  params.set("duration_seconds", String(getRequestedDuration()));
  params.set("bar_mode", getRequestedBarMode());
  params.set("range_ticks", String(getRequestedRangeTicks()));
  params.set("brick_length", String(getRequestedBrickLength()));
  params.set("data_length", String(currentRequestedDataLength()));
  if (state.selectedIndicators.length) {
    params.set("indicators", state.selectedIndicators.join(","));
    const selectedParams = {};
    state.selectedIndicators.forEach((indicatorId) => {
      selectedParams[indicatorId] = state.indicatorParams[indicatorId] || {};
    });
    params.set("indicator_params", JSON.stringify(selectedParams));
  }
  return params;
}

async function refreshConfig(provider) {
  const requestId = ++state.configRequestId;
  const params = new URLSearchParams();
  if (provider) {
    params.set("provider", provider);
  }
  const query = params.toString();
  const nextConfig = await fetchJson(`/api/config${query ? `?${query}` : ""}`);
  if (requestId !== state.configRequestId) {
    return;
  }
  state.config = nextConfig;
  state.activeProvider = nextConfig.provider;
  state.activeSymbol = nextConfig.symbol;
  state.activeDurationSeconds = nextConfig.duration_seconds;
  state.activeBarMode = nextConfig.bar_mode || "time";
  state.activeRangeTicks = nextConfig.range_ticks || state.activeRangeTicks || 10;
  state.activeBrickLength = nextConfig.brick_length || state.activeBrickLength || 10000;
  state.requestedDataLength = nextConfig.data_length || state.requestedDataLength || 800;
  state.config.provider = nextConfig.provider;
  state.config.symbol = nextConfig.symbol;
  state.config.duration_seconds = nextConfig.duration_seconds;
  state.config.bar_mode = nextConfig.bar_mode || "time";
  state.config.range_ticks = nextConfig.range_ticks || 10;
  buildProviderOptions(nextConfig.providers || [], nextConfig.provider);
  buildContractOptions(nextConfig.contracts || [], nextConfig.symbol);
  buildBarModeOptions(nextConfig.bar_modes || [{ id: "time", label: "时间 K 线" }], state.activeBarMode);
  buildDurationOptions(nextConfig.duration_options || [nextConfig.duration_seconds], nextConfig.duration_seconds);
  els.providerSelect.value = nextConfig.provider;
  els.symbolSelect.value = nextConfig.symbol;
  els.barModeSelect.value = state.activeBarMode;
  els.durationSelect.value = String(nextConfig.duration_seconds);
  els.rangeTicksInput.value = String(state.activeRangeTicks);
  els.brickLengthInput.value = String(state.activeBrickLength);
  syncMarketHeader(
    nextConfig.symbol_label || nextConfig.symbol,
    nextConfig.duration_seconds,
    state.activeBarMode,
    state.activeRangeTicks
  );
  renderProviderMeta(nextConfig);
  syncAutoRefresh(nextConfig.refresh_ms ?? 0);
}

function syncAutoRefresh(refreshMs) {
  if (state.refreshTimerId) {
    window.clearInterval(state.refreshTimerId);
    state.refreshTimerId = null;
  }
  if (shouldUseBrowserPush()) {
    return;
  }
  const effectiveRefreshMs = refreshMs;
  if (!Number.isFinite(effectiveRefreshMs) || effectiveRefreshMs <= 0) {
    return;
  }
  state.refreshTimerId = window.setInterval(async () => {
    if (state.snapshotRefreshInFlight) {
      return;
    }
    try {
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  }, effectiveRefreshMs);
}

function resizeCharts() {
  state.charts.forEach((entry) => {
    entry.chart.applyOptions({
      width: entry.container.clientWidth,
      height: entry.container.clientHeight,
    });
  });
  state.seriesByKey.forEach((series) => {
    if (typeof series?.resize === "function") {
      series.resize();
    }
  });
  updatePaneLabelPositions();
}

async function boot() {
  state.config = await fetchJson("/api/config");
  state.watchlistSymbols = loadWatchlistSymbols();
  state.activeProvider = state.config.provider;
  state.activeSymbol = state.config.symbol;
  state.activeDurationSeconds = state.config.duration_seconds;
  state.activeBarMode = state.config.bar_mode || "time";
  state.activeRangeTicks = state.config.range_ticks || 10;
  state.activeBrickLength = state.config.brick_length || 10000;
  state.requestedDataLength = state.config.data_length || 800;
  state.timeLabels = new Map();
  state.selectedIndicators = [...state.config.default_indicator_ids];
  state.indicatorParams = getDefaultIndicatorParams(state.config.indicators);

  els.provider.textContent = state.config.provider;
  buildProviderOptions(state.config.providers || [state.config.provider], state.config.provider);
  buildContractOptions(state.config.contracts || [], state.config.symbol);
  buildBarModeOptions(state.config.bar_modes || [{ id: "time", label: "时间 K 线" }], state.activeBarMode);
  buildDurationOptions(state.config.duration_options || [state.config.duration_seconds], state.config.duration_seconds);
  if (els.toolbarProvider) {
    els.toolbarProvider.value = state.config.provider;
  }
  if (els.toolbarSymbol) {
    els.toolbarSymbol.value = state.config.symbol;
  }
  if (els.toolbarDuration) {
    els.toolbarDuration.value = String(state.config.duration_seconds);
  }
  if (els.toolbarBarMode) {
    els.toolbarBarMode.value = state.activeBarMode;
  }
  els.rangeTicksInput.value = String(state.activeRangeTicks);
  els.brickLengthInput.value = String(state.activeBrickLength);
  if (els.toolbarRangeTicks) {
    els.toolbarRangeTicks.value = String(state.activeRangeTicks);
  }
  if (els.toolbarBrickLength) {
    els.toolbarBrickLength.value = String(state.activeBrickLength);
  }
  syncToolbarToggles();
  syncBarModeControls(state.activeBarMode);
  syncMarketHeader(
    state.config.symbol_label || state.config.symbol,
    state.config.duration_seconds,
    state.activeBarMode,
    state.activeRangeTicks
  );
  renderProviderMeta(state.config);
  rebuildWsTimeIndex({ candles: [], time_labels: {} });
  const savedTemplate = loadSavedTerminalTemplate();
  if (savedTemplate) {
    try {
      await applyTerminalTemplate(savedTemplate);
    } catch (error) {
      els.error.textContent = error.message;
    }
  }

  els.symbolSelect.addEventListener("change", async () => {
    try {
      state.config.symbol = getRequestedSymbol();
      els.error.textContent = "";
      syncRealtimeTransport();
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.watchlistTabFavorites?.addEventListener("click", async () => {
    state.watchlistMode = "favorites";
    buildContractOptions(state.config.contracts || [], state.activeSymbol);
  });
  els.watchlistTabAll?.addEventListener("click", async () => {
    state.watchlistMode = "all";
    buildContractOptions(state.config.contracts || [], state.activeSymbol);
  });
  els.watchlistAddCurrent?.addEventListener("click", () => {
    const current = getRequestedSymbol();
    if (!current) {
      return;
    }
    persistWatchlistSymbols([...state.watchlistSymbols, current]);
    syncWatchlistUi();
  });
  els.watchlistRemoveCurrent?.addEventListener("click", async () => {
    const current = getRequestedSymbol();
    persistWatchlistSymbols(state.watchlistSymbols.filter((item) => item !== current));
    if (state.watchlistMode === "favorites") {
      buildContractOptions(state.config.contracts || [], state.activeSymbol);
    } else {
      syncWatchlistUi();
    }
  });
  els.toolbarSymbol?.addEventListener("change", async () => {
    els.symbolSelect.value = els.toolbarSymbol.value;
    els.symbolSelect.dispatchEvent(new Event("change"));
  });
  els.providerSelect.addEventListener("change", async () => {
    const nextProvider = getRequestedProvider();
    try {
      state.snapshotRequestId += 1;
      disconnectRealtimeStream();
      if (state.refreshTimerId) {
        window.clearInterval(state.refreshTimerId);
        state.refreshTimerId = null;
      }
      await refreshConfig(nextProvider);
      els.lastPrice.textContent = "--";
      els.lastUpdate.textContent = "--";
      els.cursorTime.textContent = "--";
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.toolbarProvider?.addEventListener("change", async () => {
    els.providerSelect.value = els.toolbarProvider.value;
    els.providerSelect.dispatchEvent(new Event("change"));
  });
  els.barModeSelect.addEventListener("change", async () => {
    const nextBarMode = getRequestedBarMode();
    const previousBarMode = state.activeBarMode;
    if ((nextBarMode === "renko" || nextBarMode === "range") && previousBarMode !== nextBarMode) {
      const previousDefault = defaultTicksForBarMode(previousBarMode);
      const currentTicks = getRequestedRangeTicks();
      if (currentTicks === previousDefault || !els.rangeTicksInput.value) {
        els.rangeTicksInput.value = String(defaultTicksForBarMode(nextBarMode));
      }
    }
    syncBarModeControls(nextBarMode);
    try {
      syncRealtimeTransport();
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.durationSelect.addEventListener("change", async () => {
    try {
      syncRealtimeTransport();
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.toolbarDuration?.addEventListener("change", async () => {
    els.durationSelect.value = els.toolbarDuration.value;
    els.durationSelect.dispatchEvent(new Event("change"));
  });
  els.toolbarBarMode?.addEventListener("change", async () => {
    els.barModeSelect.value = els.toolbarBarMode.value;
    els.barModeSelect.dispatchEvent(new Event("change"));
  });
  els.toolbarRangeTicks?.addEventListener("change", async () => {
    els.rangeTicksInput.value = els.toolbarRangeTicks.value;
    els.rangeTicksInput.dispatchEvent(new Event("change"));
  });
  els.toolbarBrickLength?.addEventListener("change", async () => {
    els.brickLengthInput.value = els.toolbarBrickLength.value;
    els.brickLengthInput.dispatchEvent(new Event("change"));
  });
  [
    ["text", els.toggleText],
    ["candle", els.toggleCandle],
    ["oi", els.toggleOi],
    ["nl", els.toggleNl],
    ["ns", els.toggleNs],
    ["vwap", els.toggleVwap],
  ].forEach(([key, element]) => {
    element?.addEventListener("change", async () => {
      state.terminalToggles[key] = element.checked;
      try {
        await refreshSnapshot();
      } catch (error) {
        els.error.textContent = error.message;
      }
    });
  });
  els.saveTemplate?.addEventListener("click", () => {
    persistTerminalTemplate(buildCurrentTerminalTemplate());
    if (els.metaStatus) {
      els.metaStatus.textContent = "Template saved locally";
    }
  });
  els.resetTemplate?.addEventListener("click", async () => {
    try {
      const defaults = buildDefaultTerminalTemplate();
      persistTerminalTemplate(defaults);
      await applyTerminalTemplate(defaults);
      if (els.metaStatus) {
        els.metaStatus.textContent = "Default template restored";
      }
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.rangeTicksInput.addEventListener("change", async () => {
    try {
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  els.brickLengthInput.addEventListener("change", async () => {
    try {
      await refreshSnapshot();
    } catch (error) {
      els.error.textContent = error.message;
    }
  });
  buildIndicatorSelector(state.config.indicators, state.config.default_indicator_ids);
  rebuildCharts();
  window.addEventListener("keydown", async (event) => {
    if (event.target && ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
      return;
    }
  });
  if (shouldUseBrowserPush()) {
    syncRealtimeTransport();
  } else {
    await refreshSnapshot();
  }
  window.addEventListener("beforeunload", () => {
    disconnectRealtimeStream();
  });
}

window.addEventListener("resize", resizeCharts);

boot().catch((error) => {
  els.error.textContent = error.message;
});
