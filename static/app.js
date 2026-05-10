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
  primarySeriesKeyByPane: new Map(),
  bandPrimitiveByKey: new Map(),
  currentPriceLine: null,
  hasFitted: false,
  isSyncingCrosshair: false,
  refreshTimerId: null,
  microstructureFrameId: null,
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
  wsActualToSyntheticTime: new Map(),
  wsSyntheticToActualTime: new Map(),
  wsMaxSyntheticTime: null,
  wsMaxActualTimeMs: null,
  indicatorSyncTimerId: null,
  orderflowTradeBuckets: new Map(),
  orderflowBook: { bids: [], asks: [], ts: null },
  orderflowRecentTrades: [],
  orderflowRecentTradeIds: [],
  orderflowSeenTradeIds: new Set(),
  orderflowUi: {
    rowDensityScale: 1,
    barRowGapScale: 1,
  },
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

const EMPTY_MICROSTRUCTURE_STATS = Object.freeze({
  delta: 0,
  speed: 0,
  efficiency: 0,
  close_pos: 0,
  high_zone_buy_ratio: 0,
  low_zone_sell_ratio: 0,
  buy_vol: 0,
  sell_vol: 0,
  total_vol: 0,
  trade_count: 0,
  buy_ratio: 0,
  sell_ratio: 0,
  delta_ratio: 0,
  imbalance_ratio: 0,
  dOI: 0,
});

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
const INCREMENTAL_UPDATE_MAX_NEW_BARS = 3;
const WS_RECONNECT_MS = 2000;
const INDICATOR_SYNC_MS = 1200;
const WS_STALE_MS = 20000;
const ORDERFLOW_MAX_VISIBLE_COLUMNS = 20;
const ORDERFLOW_TARGET_VISIBLE_ROWS = 18;
const ORDERFLOW_MIN_ROW_HEIGHT = 18;
const ORDERFLOW_CELL_MIN_TEXT_WIDTH = 64;
const ORDERFLOW_DOM_HALF_WIDTH = 34;
const ORDERFLOW_IMBALANCE_RATIO = 0.72;
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

class WebGLOrderflowRenderer {
  constructor(paneEntry, definition) {
    this.paneEntry = paneEntry;
    this.chart = paneEntry.chart;
    this.container = paneEntry.container;
    this.definition = definition;
    this.viewMode = definition.options?.viewMode || "profile";
    this.profileOpacity = Number(definition.options?.profileOpacity ?? 0.78);
    this.footprintOpacity = Number(definition.options?.footprintOpacity ?? 0.9);
    this.lockPriceCenter = definition.options?.lockPriceCenter !== false;
    this.showText = definition.options?.showText !== false;
    this.data = [];
    this.rows = definition.options?.rows || [];
    this.palette = {
      positive: "#12b886",
      negative: "#f03e3e",
      neutral: "#eadfce",
      text: "#5f4a35",
      grid: "rgba(92, 70, 47, 0.12)",
      background: "rgba(255, 251, 245, 0.92)",
      ...(definition.options?.palette || {}),
    };
    this.leftGutter = 92;
    this.dpr = window.devicePixelRatio || 1;
    this.colorCache = new Map();

    this.glCanvas = document.createElement("canvas");
    this.glCanvas.className = "orderflow-gl-layer";
    this.labelCanvas = document.createElement("canvas");
    this.labelCanvas.className = "orderflow-label-layer";
    this.container.append(this.glCanvas, this.labelCanvas);

    this.gl = this.glCanvas.getContext("webgl", {
      alpha: true,
      antialias: true,
      depth: false,
      stencil: false,
      premultipliedAlpha: true,
    });
    this.labelCtx = this.labelCanvas.getContext("2d");
    this.program = null;
    this.positionBuffer = null;
    this.colorBuffer = null;
    this.positionLocation = null;
    this.colorLocation = null;
    this.vertexCount = 0;
    this.animationTimerId = null;
    this.needsRender = true;
    this.hoveredRowKey = null;
    this.hoveredColumn = null;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.render());
    this.container.addEventListener("pointermove", (event) => this.handlePointerMove(event));
    this.container.addEventListener("pointerleave", () => {
      this.hoveredRowKey = null;
      this.hoveredColumn = null;
    });
    this.container.addEventListener("wheel", (event) => this.handleWheel(event), { passive: false });

    if (this.gl) {
      this.setupProgram();
    }
    this.resize();
    this.startAnimationLoop();
  }

  setupProgram() {
    const vertexSource = `
      attribute vec2 a_position;
      attribute vec4 a_color;
      varying vec4 v_color;
      void main() {
        gl_Position = vec4(a_position, 0.0, 1.0);
        v_color = a_color;
      }
    `;
    const fragmentSource = `
      precision mediump float;
      varying vec4 v_color;
      void main() {
        gl_FragColor = v_color;
      }
    `;
    const vertexShader = this.compileShader(this.gl.VERTEX_SHADER, vertexSource);
    const fragmentShader = this.compileShader(this.gl.FRAGMENT_SHADER, fragmentSource);
    if (!vertexShader || !fragmentShader) {
      return;
    }
    this.program = this.gl.createProgram();
    this.gl.attachShader(this.program, vertexShader);
    this.gl.attachShader(this.program, fragmentShader);
    this.gl.linkProgram(this.program);
    if (!this.gl.getProgramParameter(this.program, this.gl.LINK_STATUS)) {
      console.error("orderflow gl link failed", this.gl.getProgramInfoLog(this.program));
      this.program = null;
      return;
    }
    this.positionLocation = this.gl.getAttribLocation(this.program, "a_position");
    this.colorLocation = this.gl.getAttribLocation(this.program, "a_color");
    this.positionBuffer = this.gl.createBuffer();
    this.colorBuffer = this.gl.createBuffer();
  }

  compileShader(type, source) {
    const shader = this.gl.createShader(type);
    this.gl.shaderSource(shader, source);
    this.gl.compileShader(shader);
    if (!this.gl.getShaderParameter(shader, this.gl.COMPILE_STATUS)) {
      console.error("orderflow gl shader failed", this.gl.getShaderInfoLog(shader));
      return null;
    }
    return shader;
  }

  setData(data) {
    this.data = Array.isArray(data) ? data : [];
    this.requestRender();
  }

  setDefinitionOptions(options) {
    this.viewMode = options?.viewMode || "profile";
    this.profileOpacity = Number(options?.profileOpacity ?? 0.78);
    this.footprintOpacity = Number(options?.footprintOpacity ?? 0.9);
    this.lockPriceCenter = options?.lockPriceCenter !== false;
    this.showText = options?.showText !== false;
    this.requestRender();
  }

  setMarketContext(context) {
    this.marketContext = context || null;
    this.requestRender();
  }

  resize() {
    this.dpr = window.devicePixelRatio || 1;
    const width = Math.max(this.container.clientWidth, 1);
    const height = Math.max(this.container.clientHeight, 1);
    this.glCanvas.width = Math.round(width * this.dpr);
    this.glCanvas.height = Math.round(height * this.dpr);
    this.glCanvas.style.width = `${width}px`;
    this.glCanvas.style.height = `${height}px`;
    this.labelCanvas.width = Math.round(width * this.dpr);
    this.labelCanvas.height = Math.round(height * this.dpr);
    this.labelCanvas.style.width = `${width}px`;
    this.labelCanvas.style.height = `${height}px`;
    this.requestRender();
  }

  destroy() {
    this.resizeObserver.disconnect();
    if (this.animationTimerId) {
      window.clearInterval(this.animationTimerId);
      this.animationTimerId = null;
    }
    this.glCanvas.remove();
    this.labelCanvas.remove();
  }

  startAnimationLoop() {
    this.animationTimerId = window.setInterval(() => {
      if (this.shouldAnimate()) {
        this.render();
      } else if (this.needsRender) {
        this.render();
      }
    }, 250);
  }

  shouldAnimate() {
    return this.viewMode === "ladder" || this.viewMode === "overlay";
  }

  requestRender() {
    this.needsRender = true;
  }

  handlePointerMove(event) {
    if (!this.currentScene?.rows?.length) {
      return;
    }
    const rect = this.container.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const row = this.currentScene.rows.find((item) => y >= item.top && y < item.bottom);
    this.hoveredRowKey = row?.key || null;
    this.hoveredColumn = this.currentScene?.columns?.find((item) => {
      if (!Number.isFinite(item.centerX)) {
        return false;
      }
      return Math.abs(item.centerX - x) <= 22;
    }) || null;
    this.requestRender();
  }

  handleWheel(event) {
    event.preventDefault();
    const nextScale = event.deltaY > 0
      ? Math.min(2.4, state.orderflowUi.rowDensityScale * 1.08)
      : Math.max(0.55, state.orderflowUi.rowDensityScale / 1.08);
    state.orderflowUi.rowDensityScale = nextScale;
    this.requestRender();
  }

  render() {
    this.needsRender = false;
    this.renderGrid();
    this.renderLabels();
  }

  renderGrid() {
    if (!this.gl || !this.program) {
      return;
    }
    const width = this.container.clientWidth || 1;
    const height = this.container.clientHeight || 1;
    this.gl.viewport(0, 0, this.glCanvas.width, this.glCanvas.height);
    const bg = this.cssColorToRgb(this.palette.background, 0.92);
    this.gl.clearColor(bg.r, bg.g, bg.b, bg.a);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT);

    const positions = [];
    const colors = [];
    const scene = this.viewMode === "profile"
      ? this.buildProfileScene(width, height)
      : this.viewMode === "overlay"
        ? this.buildOverlayScene(width, height)
        : this.buildFootprintScene(width, height);
    if (scene) {
      (scene.highlightBands || []).forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
      (scene.ladderBands || []).forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
      scene.cells.forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
      (scene.separatorBars || []).forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
      (scene.depthBars || []).forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
      this.currentScene = scene;
    } else {
      this.currentScene = this.buildMetricScene(width, height);
      (this.currentScene?.cells || []).forEach((cell) => {
        this.pushRect(positions, colors, cell.left, cell.top, cell.right, cell.bottom, width, height, cell.color);
      });
    }

    this.vertexCount = positions.length / 2;
    if (this.vertexCount === 0) {
      return;
    }

    this.gl.useProgram(this.program);
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.positionBuffer);
    this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(positions), this.gl.STATIC_DRAW);
    this.gl.enableVertexAttribArray(this.positionLocation);
    this.gl.vertexAttribPointer(this.positionLocation, 2, this.gl.FLOAT, false, 0, 0);

    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.colorBuffer);
    this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(colors), this.gl.STATIC_DRAW);
    this.gl.enableVertexAttribArray(this.colorLocation);
    this.gl.vertexAttribPointer(this.colorLocation, 4, this.gl.FLOAT, false, 0, 0);
    this.gl.drawArrays(this.gl.TRIANGLES, 0, this.vertexCount);
  }

  renderLabels() {
    if (!this.showText && this.currentScene?.type === "footprint") {
      this.renderModeHud(this.container.clientWidth || 1);
      return;
    }
    if (!this.labelCtx) {
      return;
    }
    const width = this.container.clientWidth || 1;
    const height = this.container.clientHeight || 1;
    this.labelCtx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.labelCtx.clearRect(0, 0, width, height);
    if (this.currentScene?.type === "footprint") {
      this.renderFootprintLabels(width, height, this.currentScene);
      this.renderModeHud(width);
      return;
    }
    if (this.currentScene?.type === "profile") {
      this.renderProfileLabels(width, height, this.currentScene);
      this.renderModeHud(width);
      return;
    }
    this.renderMetricLabels(width, height);
    this.renderModeHud(width);
  }

  renderModeHud(width) {
    const modeLabel = this.viewMode === "overlay" ? "Overlay" : this.viewMode === "ladder" ? "Ladder" : "Profile";
    const density = state.orderflowUi.rowDensityScale.toFixed(2);
    const lockText = this.lockPriceCenter ? "Center:Lock" : "Center:Free";
    const hudText = `${modeLabel}  ${lockText}  Dense:${density}  HTTP:POLL`;
    const x = width - 290;
    const y = 10;
    this.labelCtx.fillStyle = "rgba(255, 251, 245, 0.88)";
    this.labelCtx.fillRect(x, y, 280, 40);
    this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.16)";
    this.labelCtx.strokeRect(x, y, 280, 40);
    this.labelCtx.font = "11px IBM Plex Mono, IBM Plex Sans, PingFang SC, Microsoft YaHei, monospace";
    this.labelCtx.textBaseline = "middle";
    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.86)";
    this.labelCtx.fillText(hudText, x + 8, y + 12);
    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.58)";
    this.labelCtx.fillText("1 Profile  2 Overlay  3 Ladder  C Lock", x + 8, y + 29);
  }

  renderProfileLabels(width, height, scene) {
    const { rows, columns, leftGutter, rightGutter } = scene;
    this.labelCtx.fillStyle = this.palette.background;
    this.labelCtx.fillRect(0, 0, leftGutter - 8, height);
    this.labelCtx.fillRect(width - rightGutter, 0, rightGutter, height);
    this.labelCtx.font = "11px IBM Plex Mono, IBM Plex Sans, PingFang SC, Microsoft YaHei, monospace";
    this.labelCtx.textBaseline = "middle";
    this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.08)";

    rows.forEach((row, rowIndex) => {
      if (rows.length > 26 && rowIndex % 2 === 1) {
        return;
      }
      this.labelCtx.fillStyle = this.palette.text;
      this.labelCtx.fillText(row.label, 12, row.centerY);
      this.labelCtx.beginPath();
      this.labelCtx.moveTo(0, row.top + 0.5);
      this.labelCtx.lineTo(width, row.top + 0.5);
      this.labelCtx.stroke();
    });

    columns.forEach((column) => {
      this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.72)";
      this.labelCtx.fillText(column.label, column.centerX - 18, 12);
      if (column.pocPriceLabel) {
        this.labelCtx.fillStyle = "rgba(168, 116, 54, 0.92)";
        this.labelCtx.fillText(column.pocPriceLabel, column.centerX - 22, height - 12);
      }
      if (column.valueAreaLabels) {
        this.labelCtx.fillStyle = "rgba(57, 100, 176, 0.88)";
        this.labelCtx.fillText(column.valueAreaLabels.vah, column.centerX - 22, column.valueAreaLabels.vahY);
        this.labelCtx.fillText(column.valueAreaLabels.val, column.centerX - 22, column.valueAreaLabels.valY);
      }
    });

    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.82)";
    this.labelCtx.fillText("Price", 12, 12);
    this.labelCtx.fillText("Profile", width - rightGutter + 8, 12);
    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.64)";
    this.labelCtx.fillText("VRP", width - rightGutter + 56, 12);
    if (scene.summaryText) {
      this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.68)";
      this.labelCtx.fillText(scene.summaryText, width - rightGutter + 8, 28);
    }
    if (scene.headerStats) {
      this.labelCtx.fillStyle = "rgba(225, 229, 236, 0.88)";
      this.labelCtx.fillText(scene.headerStats.primary, 160, 12);
      this.labelCtx.fillStyle = "rgba(141, 147, 165, 0.82)";
      this.labelCtx.fillText(scene.headerStats.secondary, 160, 28);
    }
    if (this.hoveredColumn) {
      const hoverText1 = `T ${this.hoveredColumn.label}  POC ${this.hoveredColumn.pocPriceLabel || "--"}  VAH ${this.hoveredColumn.valueAreaLabels?.vah || "--"}  VAL ${this.hoveredColumn.valueAreaLabels?.val || "--"}`;
      const hoverText2 = `Vol ${Math.round(this.hoveredColumn.clusterVolume || 0)}  Δ ${Number(this.hoveredColumn.clusterDelta || 0).toFixed(2)}  CVD ${Number(this.hoveredColumn.clusterCvd || 0).toFixed(2)}`;
      const hoverText3 = `Spread ${this.hoveredColumn.clusterSpread || "--"}  VRP active`;
      this.labelCtx.fillStyle = "rgba(20, 21, 27, 0.94)";
      this.labelCtx.fillRect(width - 364, 42, 348, 60);
      this.labelCtx.strokeStyle = "rgba(133, 137, 153, 0.22)";
      this.labelCtx.strokeRect(width - 364, 42, 348, 60);
      this.labelCtx.fillStyle = "rgba(225, 229, 236, 0.92)";
      this.labelCtx.fillText(hoverText1, width - 356, 54);
      this.labelCtx.fillStyle = "rgba(141, 147, 165, 0.9)";
      this.labelCtx.fillText(hoverText2, width - 356, 72);
      this.labelCtx.fillText(hoverText3, width - 356, 88);
    }
  }

  renderMetricLabels(width, height) {
    this.labelCtx.fillStyle = this.palette.background;
    this.labelCtx.fillRect(0, 0, this.leftGutter - 8, height);
    this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.12)";
    this.labelCtx.lineWidth = 1;
    this.labelCtx.beginPath();
    this.labelCtx.moveTo(this.leftGutter - 0.5, 0);
    this.labelCtx.lineTo(this.leftGutter - 0.5, height);
    this.labelCtx.stroke();

    const rowHeight = height / Math.max(this.rows.length, 1);
    this.labelCtx.font = "12px IBM Plex Sans, PingFang SC, Microsoft YaHei, sans-serif";
    this.labelCtx.textBaseline = "middle";
    for (let rowIndex = 0; rowIndex < this.rows.length; rowIndex += 1) {
      const row = this.rows[rowIndex];
      const y = rowIndex * rowHeight + rowHeight / 2;
      this.labelCtx.fillStyle = this.palette.text;
      this.labelCtx.fillText(row.label, 10, y);
      const latest = this.latestMetricValue(row.key);
      this.labelCtx.fillStyle = latest.color;
      this.labelCtx.fillText(latest.text, this.leftGutter - 48, y);
      this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.08)";
      this.labelCtx.beginPath();
      this.labelCtx.moveTo(0, rowIndex * rowHeight + 0.5);
      this.labelCtx.lineTo(width, rowIndex * rowHeight + 0.5);
      this.labelCtx.stroke();
    }
  }

  renderFootprintLabels(width, height, scene) {
    const { rows, columns, rightGutter } = scene;
    this.labelCtx.fillStyle = this.palette.background;
    this.labelCtx.fillRect(0, 0, this.leftGutter - 8, height);
    this.labelCtx.fillRect(width - rightGutter, 0, rightGutter, height);
    this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.12)";
    this.labelCtx.lineWidth = 1;
    this.labelCtx.font = "11px IBM Plex Mono, IBM Plex Sans, PingFang SC, Microsoft YaHei, monospace";
    this.labelCtx.textBaseline = "middle";
    const showEveryRow = rows.length <= 22;

    rows.forEach((row, rowIndex) => {
      if (!showEveryRow && rowIndex % 2 === 1) {
        return;
      }
      this.labelCtx.fillStyle = this.palette.text;
      this.labelCtx.fillText(row.label, 12, row.centerY);
      this.labelCtx.strokeStyle = "rgba(92, 70, 47, 0.08)";
      this.labelCtx.beginPath();
      this.labelCtx.moveTo(0, row.top + 0.5);
      this.labelCtx.lineTo(width, row.top + 0.5);
      this.labelCtx.stroke();
      if (row.key === this.hoveredRowKey) {
        this.labelCtx.fillStyle = "rgba(162, 79, 47, 0.08)";
        this.labelCtx.fillRect(0, row.top, width, row.bottom - row.top);
      }
    });

    columns.forEach((column) => {
      this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.64)";
      this.labelCtx.fillText(column.label, column.left + 8, 12);
    });

    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.82)";
    this.labelCtx.fillText("Price", 12, 12);
    this.labelCtx.textAlign = "right";
    this.labelCtx.fillText("Bid", this.leftGutter + 44, 12);
    this.labelCtx.textAlign = "left";
    this.labelCtx.fillText("Ask", this.leftGutter + 52, 12);

    (scene.ladderSeparators || []).forEach((separator) => {
      this.labelCtx.strokeStyle = separator.color;
      this.labelCtx.beginPath();
      this.labelCtx.moveTo(separator.x, separator.top);
      this.labelCtx.lineTo(separator.x, separator.bottom);
      this.labelCtx.stroke();
    });

    scene.textCells.forEach((cell) => {
      if (cell.width < ORDERFLOW_CELL_MIN_TEXT_WIDTH || cell.height < 16) {
        return;
      }
      const centerY = cell.top + cell.height / 2;
      this.labelCtx.fillStyle = cell.bidColor;
      this.labelCtx.textAlign = "right";
      this.labelCtx.fillText(cell.bidText, cell.left + cell.width * 0.48, centerY);
      this.labelCtx.fillStyle = "rgba(126, 96, 69, 0.45)";
      this.labelCtx.fillText("|", cell.left + cell.width * 0.5, centerY);
      this.labelCtx.fillStyle = cell.askColor;
      this.labelCtx.textAlign = "left";
      this.labelCtx.fillText(cell.askText, cell.left + cell.width * 0.52, centerY);
      if (cell.markerText) {
        this.labelCtx.fillStyle = cell.markerColor;
        this.labelCtx.textAlign = "center";
        this.labelCtx.fillText(cell.markerText, cell.left + cell.width * 0.5, centerY - 10);
      }
    });
    this.labelCtx.textAlign = "left";

    this.labelCtx.fillStyle = this.palette.text;
    this.labelCtx.fillText("DOM", width - rightGutter + 8, 12);
    if (scene.summaryText) {
      this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.7)";
      this.labelCtx.fillText(scene.summaryText, width - rightGutter + 8, 28);
    }
    this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.72)";
    this.labelCtx.fillText("BidΣ", width - rightGutter + 8, 44);
    this.labelCtx.fillText("Bid", width - rightGutter + 46, 44);
    this.labelCtx.fillText("Ask", width - rightGutter + 82, 44);
    this.labelCtx.fillText("AskΣ", width - rightGutter + 114, 44);
    scene.depthTexts.forEach((item) => {
      if (item.rowKey === this.hoveredRowKey) {
        this.labelCtx.fillStyle = "rgba(162, 79, 47, 0.08)";
        this.labelCtx.fillRect(width - rightGutter, item.top, rightGutter, item.bottom - item.top);
      }
      this.labelCtx.fillStyle = item.bidColor;
      this.labelCtx.fillText(item.bidCumText, width - rightGutter + 8, item.centerY);
      this.labelCtx.fillText(item.bidText, width - rightGutter + 46, item.centerY);
      this.labelCtx.fillStyle = "rgba(95, 74, 53, 0.48)";
      this.labelCtx.fillText("|", width - rightGutter + 76, item.centerY);
      this.labelCtx.fillStyle = item.askColor;
      this.labelCtx.fillText(item.askText, width - rightGutter + 82, item.centerY);
      this.labelCtx.fillText(item.askCumText, width - rightGutter + 114, item.centerY);
      if (item.tagText) {
        this.labelCtx.fillStyle = item.tagColor;
        this.labelCtx.fillText(item.tagText, width - rightGutter + 8, item.centerY - 10);
      }
    });
  }

  buildMetricScene(width, height) {
    if (!this.data.length || !this.rows.length) {
      return null;
    }
    const coordinates = this.data.map((point) => this.chart.timeScale().timeToCoordinate(point.time));
    const rowHeight = height / this.rows.length;
    const cells = [];
    for (let columnIndex = 0; columnIndex < this.data.length; columnIndex += 1) {
      const x = coordinates[columnIndex];
      if (!Number.isFinite(x)) {
        continue;
      }
      const previousX = columnIndex > 0 ? coordinates[columnIndex - 1] : null;
      const nextX = columnIndex < coordinates.length - 1 ? coordinates[columnIndex + 1] : null;
      const left = this.leftGutter + Math.max(0, Number.isFinite(previousX) ? (previousX + x) / 2 : x - this.inferBarWidth(x, nextX));
      const right = Math.min(width, this.leftGutter + (Number.isFinite(nextX) ? (x + nextX) / 2 : x + this.inferBarWidth(previousX, x)));
      if (!Number.isFinite(left) || !Number.isFinite(right) || right <= left) {
        continue;
      }
      for (let rowIndex = 0; rowIndex < this.rows.length; rowIndex += 1) {
        const row = this.rows[rowIndex];
        const top = rowIndex * rowHeight + 1;
        const bottom = top + rowHeight - 2;
        const value = Number(this.data[columnIndex]?.[row.key]);
        const color = this.metricColor(value, row);
        cells.push({ left, right, top, bottom, color });
      }
    }
    return { type: "metric", cells };
  }

  buildFootprintScene(width, height) {
    const candles = this.marketContext?.candles || [];
    const tradeBuckets = this.marketContext?.tradeBuckets;
    if (!candles.length || !(tradeBuckets instanceof Map) || tradeBuckets.size === 0) {
      return null;
    }

    const visibleCandles = candles.slice(-Math.min(candles.length, ORDERFLOW_MAX_VISIBLE_COLUMNS));
    const columns = visibleCandles
      .map((candle, index) => {
        const x = this.chart.timeScale().timeToCoordinate(candle.time);
        if (!Number.isFinite(x)) {
          return null;
        }
        return {
          time: candle.time,
          x,
          label: (this.marketContext?.timeLabels?.get(String(candle.time)) || "").slice(11, 16),
          index,
        };
      })
      .filter(Boolean);
    if (columns.length === 0) {
      return null;
    }

    const rightGutter = 164;
    const levelsMap = new Map();
    visibleCandles.forEach((candle) => {
      const actualTimeMs = this.marketContext?.syntheticToActualTime?.get(Number(candle.time));
      const bucket = tradeBuckets.get(String(actualTimeMs));
      if (!bucket) {
        return;
      }
      bucket.levels.forEach((level, key) => {
        levelsMap.set(key, level.price);
      });
    });
    [...(this.marketContext?.orderBook?.bids || []), ...(this.marketContext?.orderBook?.asks || [])].forEach((level) => {
      levelsMap.set(orderflowPriceKey(level.price), level.price);
    });
    const sortedLevels = [...levelsMap.values()].sort((a, b) => b - a);
    const bestBid = Number(this.marketContext?.orderBook?.bids?.[0]?.price);
    const bestAsk = Number(this.marketContext?.orderBook?.asks?.[0]?.price);
    const referencePrice = Number.isFinite(bestBid) && Number.isFinite(bestAsk)
      ? (bestBid + bestAsk) / 2
      : Number.isFinite(bestBid)
        ? bestBid
        : Number.isFinite(bestAsk)
          ? bestAsk
          : sortedLevels[Math.floor(sortedLevels.length / 2)];
    const priceTick = this.derivePriceTick(sortedLevels);
    let centerIndex = sortedLevels.findIndex((price) => price <= referencePrice);
    if (centerIndex < 0) {
      centerIndex = Math.floor(sortedLevels.length / 2);
    }
    const visibleRowCount = Math.max(
      10,
      Math.min(
        34,
        Math.round((Math.floor(height / ORDERFLOW_MIN_ROW_HEIGHT) || ORDERFLOW_TARGET_VISIBLE_ROWS) / state.orderflowUi.rowDensityScale),
        ORDERFLOW_TARGET_VISIBLE_ROWS + 6
      )
    );
    const anchorIndex = this.lockPriceCenter ? centerIndex : Math.floor(sortedLevels.length / 2);
    const fixedWindowHalf = Math.floor(visibleRowCount / 2);
    const startIndex = Math.max(0, Math.min(sortedLevels.length - visibleRowCount, anchorIndex - fixedWindowHalf));
    const priceLevels = sortedLevels.slice(startIndex, startIndex + visibleRowCount);
    if (priceLevels.length === 0) {
      return null;
    }

    const rowHeight = height / priceLevels.length;
    const rows = priceLevels.map((price, index) => ({
      price,
      key: orderflowPriceKey(price),
      label: price.toFixed(2),
      top: index * rowHeight,
      bottom: index * rowHeight + rowHeight,
      centerY: index * rowHeight + rowHeight / 2,
    }));

    const cells = [];
    const textCells = [];
    const separatorBars = [];
    const highlightBands = [];
    const ladderBands = [];
    const ladderSeparators = [];
    const depthBars = [];
    const depthTexts = [];
    const allDepthLevels = [...(this.marketContext?.orderBook?.bids || []), ...(this.marketContext?.orderBook?.asks || [])];
    const maxDepthSize = Math.max(
      1,
      ...allDepthLevels.map((item) => item.size || 0)
    );
    const bucketTotals = [];
    visibleCandles.forEach((candle) => {
      const actualTimeMs = this.marketContext?.syntheticToActualTime?.get(Number(candle.time));
      const bucket = tradeBuckets.get(String(actualTimeMs));
      if (!bucket) {
        return;
      }
      bucket.levels.forEach((level) => {
        const total = Number(level?.total || 0);
        if (Number.isFinite(total) && total > 0) {
          bucketTotals.push(total);
        }
      });
    });
    bucketTotals.sort((a, b) => a - b);
    const largeTradeThreshold = bucketTotals.length
      ? bucketTotals[Math.max(0, Math.floor(bucketTotals.length * 0.88) - 1)]
      : Number.POSITIVE_INFINITY;
    const pulseAlpha = 0.08 + ((Math.sin(performance.now() / 380) + 1) / 2) * 0.18;

    columns.forEach((column, index) => {
      const previousX = index > 0 ? columns[index - 1].x : null;
      const nextX = index < columns.length - 1 ? columns[index + 1].x : null;
      const left = this.leftGutter + Math.max(0, Number.isFinite(previousX) ? (previousX + column.x) / 2 : column.x - this.inferBarWidth(column.x, nextX));
      const right = Math.min(width - rightGutter, this.leftGutter + (Number.isFinite(nextX) ? (column.x + nextX) / 2 : column.x + this.inferBarWidth(previousX, column.x)));
      if (!Number.isFinite(left) || !Number.isFinite(right) || right <= left) {
        return;
      }
      const midX = left + (right - left) / 2;
      const actualTimeMs = this.marketContext?.syntheticToActualTime?.get(Number(column.time));
      const bucket = tradeBuckets.get(String(actualTimeMs));
      const levels = bucket?.levels || new Map();
      const tradedRows = [];

      rows.forEach((row, rowIndex) => {
        const level = levels.get(row.key);
        const buy = Number(level?.buy || 0);
        const sell = Number(level?.sell || 0);
        const total = Number(level?.total || 0);
        if (total <= 0) {
          return;
        }
        tradedRows.push({ row, rowIndex, buy, sell, total });
        const totalStrength = Math.max(0, Math.min(total / 8, 1));
        const buyDominance = Math.max(0, Math.min(buy / Math.max(total, 1e-9), 1));
        const sellDominance = Math.max(0, Math.min(sell / Math.max(total, 1e-9), 1));
        const sweepBias = Math.abs(buy - sell) >= Math.max(8, total * 0.66);
        const nextLowerRow = rows[rowIndex + 1];
        const nextHigherRow = rows[rowIndex - 1];
        const nextLowerLevel = nextLowerRow ? levels.get(nextLowerRow.key) : null;
        const nextHigherLevel = nextHigherRow ? levels.get(nextHigherRow.key) : null;
        const stackedAsk = sell > 0 && Number(nextLowerLevel?.buy || 0) > 0 && sell >= Number(nextLowerLevel.buy) * 2.5;
        const stackedBid = buy > 0 && Number(nextHigherLevel?.sell || 0) > 0 && buy >= Number(nextHigherLevel.sell) * 2.5;
        const buyColor = this.mixColors(this.palette.neutral, this.palette.positive, buyDominance, (0.14 + totalStrength * 0.74) * this.footprintOpacity);
        const sellColor = this.mixColors(this.palette.neutral, this.palette.negative, sellDominance, (0.14 + totalStrength * 0.74) * this.footprintOpacity);
        cells.push({
          left,
          right: midX,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: buyColor,
        });
        cells.push({
          left: midX,
          right,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: sellColor,
        });
        separatorBars.push({
          left: midX - 0.5,
          right: midX + 0.5,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: this.cssColorToRgb("rgba(126, 96, 69, 0.28)", 0.35),
        });
        textCells.push({
          left,
          top: row.top + 1,
          width: right - left,
          height: row.bottom - row.top - 2,
          bidText: buy > 0 ? buy.toFixed(buy >= 10 ? 0 : 1) : "",
          askText: sell > 0 ? sell.toFixed(sell >= 10 ? 0 : 1) : "",
          bidColor: buy > sell ? "rgba(7, 101, 73, 0.95)" : "rgba(7, 101, 73, 0.78)",
          askColor: sell > buy ? "rgba(155, 30, 30, 0.95)" : "rgba(155, 30, 30, 0.78)",
          markerText: total >= largeTradeThreshold
            ? "BLK"
            : stackedAsk
              ? "STA"
              : stackedBid
                ? "STB"
            : sweepBias
              ? (buy > sell ? "SWP↑" : "SWP↓")
              : (buyDominance >= ORDERFLOW_IMBALANCE_RATIO ? "B↑" : (sellDominance >= ORDERFLOW_IMBALANCE_RATIO ? "S↓" : "")),
          markerColor: total >= largeTradeThreshold
            ? "rgba(168, 116, 54, 0.98)"
            : stackedAsk
              ? "rgba(155, 30, 30, 0.98)"
              : stackedBid
                ? "rgba(7, 101, 73, 0.98)"
            : sweepBias
              ? (buy > sell ? "rgba(7, 101, 73, 0.98)" : "rgba(155, 30, 30, 0.98)")
            : buyDominance >= ORDERFLOW_IMBALANCE_RATIO
              ? "rgba(7, 101, 73, 0.98)"
              : "rgba(155, 30, 30, 0.98)",
        });
      });

      if (tradedRows.length > 0) {
        const topTrade = tradedRows[0];
        const bottomTrade = tradedRows[tradedRows.length - 1];
        const topUnfinished = topTrade.buy > 0 && topTrade.sell > 0;
        const bottomUnfinished = bottomTrade.buy > 0 && bottomTrade.sell > 0;
        textCells.push({
          left,
          top: topTrade.row.top + 1,
          width: right - left,
          height: topTrade.row.bottom - topTrade.row.top - 2,
          bidText: "",
          askText: "",
          bidColor: "rgba(7, 101, 73, 0.9)",
          askColor: "rgba(155, 30, 30, 0.9)",
          markerText: topUnfinished ? "UA↑" : "EXH↑",
          markerColor: topUnfinished ? "rgba(168, 116, 54, 0.98)" : "rgba(155, 30, 30, 0.92)",
        });
        if (bottomTrade.row.key !== topTrade.row.key) {
          textCells.push({
            left,
            top: bottomTrade.row.top + 1,
            width: right - left,
            height: bottomTrade.row.bottom - bottomTrade.row.top - 2,
            bidText: "",
            askText: "",
            bidColor: "rgba(7, 101, 73, 0.9)",
            askColor: "rgba(155, 30, 30, 0.9)",
            markerText: bottomUnfinished ? "UA↓" : "EXH↓",
            markerColor: bottomUnfinished ? "rgba(168, 116, 54, 0.98)" : "rgba(7, 101, 73, 0.92)",
          });
        }
      }
    });

    let cumulativeBid = 0;
    let cumulativeAsk = 0;
    const ladderLeft = width - rightGutter;
    [36, 74, 110, 146].forEach((offset) => {
      ladderSeparators.push({
        x: ladderLeft + offset,
        top: 36,
        bottom: height,
        color: "rgba(92, 70, 47, 0.12)",
      });
    });
    rows.forEach((row, rowIndex) => {
      const bid = (this.marketContext?.orderBook?.bids || []).find((item) => orderflowPriceKey(item.price) === row.key);
      const ask = (this.marketContext?.orderBook?.asks || []).find((item) => orderflowPriceKey(item.price) === row.key);
      const nextLowerRow = rows[rowIndex + 1];
      const nextBid = nextLowerRow
        ? (this.marketContext?.orderBook?.bids || []).find((item) => orderflowPriceKey(item.price) === nextLowerRow.key)
        : null;
      const domHalfWidth = ORDERFLOW_DOM_HALF_WIDTH;
      const bidWidth = bid ? (domHalfWidth * (bid.size / maxDepthSize)) : 0;
      const askWidth = ask ? (domHalfWidth * (ask.size / maxDepthSize)) : 0;
      const domMid = width - rightGutter + 56;
      ladderBands.push({
        left: width - rightGutter,
        right: width,
        top: row.top,
        bottom: row.bottom,
        color: this.cssColorToRgb(rowIndex % 2 === 0 ? "rgba(92, 70, 47, 0.025)" : "rgba(92, 70, 47, 0.055)", 1),
      });
      if (bidWidth > 0) {
        depthBars.push({
          left: domMid - bidWidth,
          right: domMid,
          top: row.top + 2,
          bottom: row.bottom - 2,
          color: this.mixColors(this.palette.neutral, this.palette.positive, 0.85, 0.45 * this.footprintOpacity),
        });
      }
      if (askWidth > 0) {
        depthBars.push({
          left: domMid,
          right: domMid + askWidth,
          top: row.top + 2,
          bottom: row.bottom - 2,
          color: this.mixColors(this.palette.neutral, this.palette.negative, 0.85, 0.45 * this.footprintOpacity),
        });
      }
      cumulativeBid += bid ? bid.size : 0;
      cumulativeAsk += ask ? ask.size : 0;
      const bidSize = bid ? bid.size : 0;
      const askSize = ask ? ask.size : 0;
      const totalDepth = bidSize + askSize;
      const bidRatio = totalDepth > 0 ? bidSize / totalDepth : 0;
      const askRatio = totalDepth > 0 ? askSize / totalDepth : 0;
      const stackedAskImbalance = ask && nextBid && ask.size >= nextBid.size * 2.5 && ask.size >= 5;
      const stackedBidImbalance = bid && nextLowerRow
        ? (() => {
            const upperAsk = ask;
            return bid && upperAsk && bid.size >= upperAsk.size * 2.5 && bid.size >= 5;
          })()
        : false;
      depthTexts.push({
        centerY: row.centerY,
        top: row.top,
        bottom: row.bottom,
        rowKey: row.key,
        bidColor: bid ? "rgba(7, 101, 73, 0.96)" : "rgba(95, 74, 53, 0.30)",
        askColor: ask ? "rgba(155, 30, 30, 0.96)" : "rgba(95, 74, 53, 0.30)",
        bidCumText: bid ? cumulativeBid.toFixed(cumulativeBid >= 10 ? 0 : 1) : "-",
        askCumText: ask ? cumulativeAsk.toFixed(cumulativeAsk >= 10 ? 0 : 1) : "-",
        bidText: bid ? bid.size.toFixed(bid.size >= 10 ? 0 : 1) : "-",
        askText: ask ? ask.size.toFixed(ask.size >= 10 ? 0 : 1) : "-",
        tagText: stackedAskImbalance ? "STACK A" : (stackedBidImbalance ? "STACK B" : (bidRatio >= ORDERFLOW_IMBALANCE_RATIO ? "BID IMB" : (askRatio >= ORDERFLOW_IMBALANCE_RATIO ? "ASK IMB" : ""))),
        tagColor: stackedAskImbalance
          ? "rgba(155, 30, 30, 0.98)"
          : stackedBidImbalance
            ? "rgba(7, 101, 73, 0.98)"
            : bidRatio >= ORDERFLOW_IMBALANCE_RATIO
              ? "rgba(7, 101, 73, 0.96)"
              : "rgba(155, 30, 30, 0.96)",
      });

      const nearBestBid = Number.isFinite(bestBid) && Math.abs(row.price - bestBid) <= priceTick * 0.25;
      const nearBestAsk = Number.isFinite(bestAsk) && Math.abs(row.price - bestAsk) <= priceTick * 0.25;
      const nearMid = Number.isFinite(referencePrice) && Math.abs(row.price - referencePrice) <= priceTick * 0.25;
      if (nearBestBid) {
        highlightBands.push({
          left: this.leftGutter,
          right: width,
          top: row.top,
          bottom: row.bottom,
          color: this.cssColorToRgb("rgba(18, 184, 134, 1)", pulseAlpha),
        });
      }
      if (nearBestAsk) {
        highlightBands.push({
          left: this.leftGutter,
          right: width,
          top: row.top,
          bottom: row.bottom,
          color: this.cssColorToRgb("rgba(240, 62, 62, 1)", pulseAlpha),
        });
      }
      if (nearMid) {
        highlightBands.push({
          left: this.leftGutter,
          right: width,
          top: row.centerY - 1,
          bottom: row.centerY + 1,
          color: this.cssColorToRgb("rgba(168, 116, 54, 0.30)", 0.3),
        });
      }
    });

    const spreadTicks = Number.isFinite(bestBid) && Number.isFinite(bestAsk) && priceTick > 0
      ? ((bestAsk - bestBid) / priceTick).toFixed(1)
      : "--";

    return {
      type: "footprint",
      rows,
      columns,
      cells,
      textCells,
      separatorBars,
      highlightBands,
      ladderBands,
      ladderSeparators,
      depthBars,
      depthTexts,
      rightGutter,
      summaryText: `Spr ${spreadTicks}t  Mid ${Number.isFinite(referencePrice) ? referencePrice.toFixed(2) : "--"}`,
    };
  }

  buildProfileScene(width, height) {
    const candles = this.marketContext?.candles || [];
    const tradeBuckets = this.marketContext?.tradeBuckets;
    if (!candles.length || !(tradeBuckets instanceof Map) || tradeBuckets.size === 0) {
      return null;
    }

    const leftGutter = 72;
    const rightGutter = 132;
    const visibleCandles = candles.slice(-Math.min(candles.length, ORDERFLOW_MAX_VISIBLE_COLUMNS));
    const levelsMap = new Map();
    const columns = visibleCandles
      .map((candle, index) => {
        const x = this.chart.timeScale().timeToCoordinate(candle.time);
        if (!Number.isFinite(x)) {
          return null;
        }
        const actualTimeMs = this.marketContext?.syntheticToActualTime?.get(Number(candle.time));
        const bucket = tradeBuckets.get(String(actualTimeMs));
        if (!bucket) {
          return null;
        }
        bucket.levels.forEach((level, key) => {
          levelsMap.set(key, level.price);
        });
        return {
          time: candle.time,
          x,
          label: (this.marketContext?.timeLabels?.get(String(candle.time)) || "").slice(11, 16),
          actualTimeMs,
          bucket,
          index,
        };
      })
      .filter(Boolean);
    if (columns.length === 0) {
      return null;
    }

    const sortedLevels = [...levelsMap.values()].sort((a, b) => b - a);
    if (sortedLevels.length === 0) {
      return null;
    }
    const priceTick = this.derivePriceTick(sortedLevels);
    const bestBid = Number(this.marketContext?.orderBook?.bids?.[0]?.price);
    const bestAsk = Number(this.marketContext?.orderBook?.asks?.[0]?.price);
    const referencePrice = Number.isFinite(bestBid) && Number.isFinite(bestAsk)
      ? (bestBid + bestAsk) / 2
      : sortedLevels[Math.floor(sortedLevels.length / 2)];
    let centerIndex = sortedLevels.findIndex((price) => price <= referencePrice);
    if (centerIndex < 0) {
      centerIndex = Math.floor(sortedLevels.length / 2);
    }
    const visibleRowCount = Math.max(
      10,
      Math.min(
        36,
        Math.round((Math.floor(height / ORDERFLOW_MIN_ROW_HEIGHT) || ORDERFLOW_TARGET_VISIBLE_ROWS) / state.orderflowUi.rowDensityScale),
        ORDERFLOW_TARGET_VISIBLE_ROWS + 8
      )
    );
    const anchorIndex = this.lockPriceCenter ? centerIndex : Math.floor(sortedLevels.length / 2);
    const startIndex = Math.max(0, Math.min(sortedLevels.length - visibleRowCount, anchorIndex - Math.floor(visibleRowCount / 2)));
    const priceLevels = sortedLevels.slice(startIndex, startIndex + visibleRowCount);
    const rowHeight = height / priceLevels.length;
    const rows = priceLevels.map((price, index) => ({
      price,
      key: orderflowPriceKey(price),
      label: price.toFixed(2),
      top: index * rowHeight,
      bottom: index * rowHeight + rowHeight,
      centerY: index * rowHeight + rowHeight / 2,
    }));

    const cells = [];
    const separatorBars = [];
    const highlightBands = [];
    const depthBars = [];
    const maxColumnWidth = 28;
    const visibleProfileLevels = new Map();

    columns.forEach((column, index) => {
      const previousX = index > 0 ? columns[index - 1].x : null;
      const nextX = index < columns.length - 1 ? columns[index + 1].x : null;
      const centerX = leftGutter + column.x;
      const columnWidth = Math.max(14, Math.min(38, Number.isFinite(previousX) && Number.isFinite(nextX) ? (nextX - previousX) * 0.7 : 24));
      const levels = column.bucket.levels || new Map();
      let maxTotal = 0;
      let pocPrice = null;
      let pocTotal = -1;
      let clusterVolume = 0;
      let clusterDelta = 0;
      rows.forEach((row) => {
        const level = levels.get(row.key);
        const total = Number(level?.total || 0);
        if (total > maxTotal) {
          maxTotal = total;
        }
        if (total > pocTotal) {
          pocTotal = total;
          pocPrice = row.price;
        }
        clusterVolume += total;
        clusterDelta += Number(level?.buy || 0) - Number(level?.sell || 0);
      });
      const valueAreaTarget = [...levels.values()].reduce((sum, level) => sum + Number(level?.total || 0), 0) * 0.7;
      const ranked = rows
        .map((row) => ({ row, total: Number(levels.get(row.key)?.total || 0) }))
        .filter((item) => item.total > 0)
        .sort((a, b) => b.total - a.total);
      let valueAreaAccum = 0;
      const valueAreaKeys = new Set();
      ranked.forEach((item) => {
        if (valueAreaAccum < valueAreaTarget) {
          valueAreaAccum += item.total;
          valueAreaKeys.add(item.row.key);
        }
      });

      let vahRow = null;
      let valRow = null;
      rows.forEach((row) => {
        const level = levels.get(row.key);
        const buy = Number(level?.buy || 0);
        const sell = Number(level?.sell || 0);
        const total = Number(level?.total || 0);
        if (total <= 0 || maxTotal <= 0) {
          return;
        }
        const aggregate = visibleProfileLevels.get(row.key) || { buy: 0, sell: 0, total: 0 };
        aggregate.buy += buy;
        aggregate.sell += sell;
        aggregate.total += total;
        visibleProfileLevels.set(row.key, aggregate);
        const leftWidth = (Math.min(sell / maxTotal, 1) * maxColumnWidth);
        const rightWidth = (Math.min(buy / maxTotal, 1) * maxColumnWidth);
        const delta = buy - sell;
        const deltaWidth = Math.min(Math.abs(delta) / maxTotal, 1) * (maxColumnWidth * 0.68);
        const left = centerX - leftWidth;
        const right = centerX + rightWidth;
        const rowCenterX = centerX;
        const buyColor = this.mixColors("#1b1c21", "#6f8ecf", Math.min(buy / Math.max(total, 1e-9), 1), (0.32 + Math.min(total / Math.max(maxTotal, 1), 1) * 0.58) * this.profileOpacity);
        const sellColor = this.mixColors("#1b1c21", "#d53847", Math.min(sell / Math.max(total, 1e-9), 1), (0.32 + Math.min(total / Math.max(maxTotal, 1), 1) * 0.58) * this.profileOpacity);
        cells.push({
          left,
          right: rowCenterX,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: sellColor,
        });
        cells.push({
          left: rowCenterX,
          right,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: buyColor,
        });
        cells.push({
          left: delta >= 0 ? rowCenterX - 1.5 : rowCenterX - deltaWidth,
          right: delta >= 0 ? rowCenterX + deltaWidth : rowCenterX + 1.5,
          top: row.centerY - 1.4,
          bottom: row.centerY + 1.4,
          color: this.cssColorToRgb(delta >= 0 ? "rgba(70, 220, 160, 0.92)" : "rgba(255, 96, 96, 0.92)", 0.92 * this.profileOpacity),
        });
        separatorBars.push({
          left: rowCenterX - 0.5,
          right: rowCenterX + 0.5,
          top: row.top + 1,
          bottom: row.bottom - 1,
          color: this.cssColorToRgb("rgba(185, 202, 240, 0.18)", 0.18),
        });
        if (valueAreaKeys.has(row.key)) {
          highlightBands.push({
            left: centerX - maxColumnWidth - 2,
            right: centerX + maxColumnWidth + 2,
            top: row.top + 2,
            bottom: row.bottom - 2,
            color: this.cssColorToRgb("rgba(111, 142, 207, 0.07)", 0.07 * this.profileOpacity),
          });
          vahRow = vahRow || row;
          valRow = row;
        }
      });

      if (Number.isFinite(pocPrice)) {
        const pocRow = rows.find((row) => Math.abs(row.price - pocPrice) <= priceTick * 0.25);
        if (pocRow) {
          highlightBands.push({
            left: centerX - maxColumnWidth - 3,
            right: centerX + maxColumnWidth + 3,
            top: pocRow.centerY - 1.5,
            bottom: pocRow.centerY + 1.5,
            color: this.cssColorToRgb("rgba(255, 80, 80, 0.75)", 0.75 * this.profileOpacity),
          });
          column.pocPriceLabel = pocPrice.toFixed(2);
        }
      }

      if (vahRow && valRow) {
        highlightBands.push({
          left: centerX - maxColumnWidth - 5,
          right: centerX + maxColumnWidth + 5,
          top: vahRow.centerY - 1,
          bottom: vahRow.centerY + 1,
          color: this.cssColorToRgb("rgba(57, 100, 176, 0.92)", 0.92 * this.profileOpacity),
        });
        highlightBands.push({
          left: centerX - maxColumnWidth - 5,
          right: centerX + maxColumnWidth + 5,
          top: valRow.centerY - 1,
          bottom: valRow.centerY + 1,
          color: this.cssColorToRgb("rgba(57, 100, 176, 0.92)", 0.92 * this.profileOpacity),
        });
        column.valueAreaLabels = {
          vah: vahRow.price.toFixed(2),
          val: valRow.price.toFixed(2),
          vahY: Math.max(22, vahRow.centerY),
          valY: Math.min(height - 22, valRow.centerY),
        };
      }

      column.centerX = centerX;
      column.clusterVolume = clusterVolume;
      column.clusterDelta = clusterDelta;
      column.clusterCvd = clusterDelta;
      column.clusterSpread = spreadTicks;
    });

    const spreadTicks = Number.isFinite(bestBid) && Number.isFinite(bestAsk) && priceTick > 0
      ? ((bestAsk - bestBid) / priceTick).toFixed(1)
      : "--";
    const visibleVolume = [...visibleProfileLevels.values()].reduce((sum, item) => sum + Number(item.total || 0), 0);
    const visibleDelta = [...visibleProfileLevels.values()].reduce((sum, item) => sum + Number(item.buy || 0) - Number(item.sell || 0), 0);
    const visibleCvd = [...visibleProfileLevels.values()].reduce((sum, item) => sum + (Number(item.buy || 0) - Number(item.sell || 0)), 0);
    const bestColumn = columns.find((column) => column.pocPriceLabel) || columns[columns.length - 1];
    const vah = bestColumn?.valueAreaLabels?.vah || "--";
    const val = bestColumn?.valueAreaLabels?.val || "--";

    const aggregateMax = Math.max(1, ...[...visibleProfileLevels.values()].map((item) => item.total || 0));
    rows.forEach((row) => {
      const aggregate = visibleProfileLevels.get(row.key);
      if (!aggregate || aggregate.total <= 0) {
        return;
      }
      const profileCenter = width - rightGutter + 54;
      const halfWidth = 26;
      const askWidth = Math.min(aggregate.sell / aggregateMax, 1) * halfWidth;
      const bidWidth = Math.min(aggregate.buy / aggregateMax, 1) * halfWidth;
      depthBars.push({
        left: profileCenter - askWidth,
        right: profileCenter,
        top: row.top + 2,
        bottom: row.bottom - 2,
        color: this.cssColorToRgb("rgba(213, 56, 71, 0.55)", 0.55 * this.profileOpacity),
      });
      depthBars.push({
        left: profileCenter,
        right: profileCenter + bidWidth,
        top: row.top + 2,
        bottom: row.bottom - 2,
        color: this.cssColorToRgb("rgba(111, 142, 207, 0.55)", 0.55 * this.profileOpacity),
      });
    });

    return {
      type: "profile",
      rows,
      columns,
      cells,
      separatorBars,
      highlightBands,
      ladderBands: [],
      depthBars,
      leftGutter,
      rightGutter,
      summaryText: `VRP  Spr ${spreadTicks}t  Vol ${Math.round(visibleVolume)}`,
      headerStats: {
        primary: `POC ${bestColumn?.pocPriceLabel || "--"}   VAH ${vah}   VAL ${val}`,
        secondary: `Delta ${visibleDelta.toFixed(2)}   CVD ${visibleCvd.toFixed(2)}   Clusters ${columns.length}   VRP active`,
      },
    };
  }

  buildOverlayScene(width, height) {
    const profileScene = this.buildProfileScene(width, height);
    const footprintScene = this.buildFootprintScene(width, height);
    if (!profileScene && !footprintScene) {
      return null;
    }
    if (!profileScene) {
      return footprintScene;
    }
    if (!footprintScene) {
      return profileScene;
    }
    return {
      ...footprintScene,
      type: "footprint",
      cells: [
        ...profileScene.cells,
        ...footprintScene.cells,
      ],
      separatorBars: [
        ...(profileScene.separatorBars || []),
        ...(footprintScene.separatorBars || []),
      ],
      highlightBands: [...(profileScene.highlightBands || []), ...(footprintScene.highlightBands || [])],
      summaryText: `${profileScene.summaryText}  + Overlay`,
    };
  }

  derivePriceTick(sortedLevels) {
    for (let index = 1; index < sortedLevels.length; index += 1) {
      const diff = Math.abs(sortedLevels[index - 1] - sortedLevels[index]);
      if (Number.isFinite(diff) && diff > 0) {
        return diff;
      }
    }
    return 0.01;
  }

  latestMetricValue(key) {
    const latest = [...this.data].reverse().find((item) => Number.isFinite(Number(item?.[key])));
    const value = Number(latest?.[key]);
    if (!Number.isFinite(value)) {
      return { text: "--", color: this.palette.text };
    }
    return {
      text: Math.abs(value) >= 100 ? value.toFixed(0) : Math.abs(value) >= 10 ? value.toFixed(1) : value.toFixed(3),
      color: value >= 0 ? this.palette.positive : this.palette.negative,
    };
  }

  inferBarWidth(leftX, rightX) {
    if (Number.isFinite(leftX) && Number.isFinite(rightX)) {
      return Math.max(Math.abs(rightX - leftX) / 2, 3);
    }
    return 6;
  }

  metricColor(value, row) {
    if (!Number.isFinite(value)) {
      return this.cssColorToRgb(this.palette.neutral, 0.08);
    }
    const scale = Number(row.scale) || 1;
    const strength = Math.max(0, Math.min(Math.abs(value) / scale, 1));
    if (row.mode === "positive") {
      return this.mixColors(this.palette.neutral, this.palette.positive, strength, 0.18 + strength * 0.76);
    }
    if (value >= 0) {
      return this.mixColors(this.palette.neutral, this.palette.positive, strength, 0.22 + strength * 0.72);
    }
    return this.mixColors(this.palette.neutral, this.palette.negative, strength, 0.22 + strength * 0.72);
  }

  mixColors(fromCss, toCss, weight, alpha) {
    const from = this.cssColorToRgb(fromCss, 1);
    const to = this.cssColorToRgb(toCss, 1);
    return {
      r: from.r * (1 - weight) + to.r * weight,
      g: from.g * (1 - weight) + to.g * weight,
      b: from.b * (1 - weight) + to.b * weight,
      a: alpha,
    };
  }

  cssColorToRgb(cssColor, alpha = 1) {
    let normalized = this.colorCache.get(cssColor);
    if (!normalized) {
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.fillStyle = cssColor;
      normalized = ctx.fillStyle;
      this.colorCache.set(cssColor, normalized);
    }
    const match = normalized.match(/^#([0-9a-f]{6})$/i);
    if (match) {
      const hex = match[1];
      return {
        r: parseInt(hex.slice(0, 2), 16) / 255,
        g: parseInt(hex.slice(2, 4), 16) / 255,
        b: parseInt(hex.slice(4, 6), 16) / 255,
        a: alpha,
      };
    }
    const rgbaMatch = normalized.match(/^rgba?\(([^)]+)\)$/i);
    if (rgbaMatch) {
      const parts = rgbaMatch[1].split(",").map((item) => item.trim());
      const red = Math.max(0, Math.min(255, Number(parts[0] || 0))) / 255;
      const green = Math.max(0, Math.min(255, Number(parts[1] || 0))) / 255;
      const blue = Math.max(0, Math.min(255, Number(parts[2] || 0))) / 255;
      const parsedAlpha = parts.length > 3 ? Number(parts[3]) : 1;
      return {
        r: red,
        g: green,
        b: blue,
        a: Number.isFinite(parsedAlpha) ? parsedAlpha * alpha : alpha,
      };
    }
    return { r: 0.9, g: 0.88, b: 0.82, a: alpha };
  }

  pushRect(positions, colors, left, top, right, bottom, width, height, color) {
    const x0 = (left / width) * 2 - 1;
    const x1 = (right / width) * 2 - 1;
    const y0 = 1 - (top / height) * 2;
    const y1 = 1 - (bottom / height) * 2;
    positions.push(
      x0, y0,
      x1, y0,
      x0, y1,
      x0, y1,
      x1, y0,
      x1, y1
    );
    for (let index = 0; index < 6; index += 1) {
      colors.push(color.r, color.g, color.b, color.a);
    }
  }
}

class TerminalStatsRenderer {
  constructor(paneEntry, definition) {
    this.container = paneEntry.container;
    this.chart = paneEntry.chart;
    this.definition = definition;
    this.data = [];
    this.canvas = document.createElement("canvas");
    this.canvas.className = "orderflow-label-layer";
    this.container.append(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.dpr = window.devicePixelRatio || 1;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.render());
    this.resize();
  }

  setData(data) {
    this.data = Array.isArray(data) ? data : [];
    this.render();
  }

  setDefinitionOptions(options) {
    this.definition = { ...this.definition, options };
    this.render();
  }

  setMarketContext(context) {
    this.marketContext = context || null;
    this.render();
  }

  resize() {
    this.dpr = window.devicePixelRatio || 1;
    const width = Math.max(this.container.clientWidth, 1);
    const height = Math.max(this.container.clientHeight, 1);
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.render();
  }

  destroy() {
    this.resizeObserver.disconnect();
    this.canvas.remove();
  }

  render() {
    if (!this.ctx) {
      return;
    }
    const width = this.container.clientWidth || 1;
    const height = this.container.clientHeight || 1;
    const rows = this.definition.options?.rows || [];
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, width, height);
    this.ctx.fillStyle = "rgba(16, 17, 22, 0.96)";
    this.ctx.fillRect(0, 0, width, height);
    if (!rows.length || !this.data.length) {
      return;
    }
    const headerHeight = 16;
    const rowHeight = (height - headerHeight) / rows.length;
    const latestTime = this.data[this.data.length - 1]?.time;
    this.ctx.font = "13px IBM Plex Mono, IBM Plex Sans, monospace";
    this.ctx.fillStyle = "rgba(141, 147, 165, 0.82)";
    this.ctx.fillText("Footprint bar statistics", 8, 11);
    this.ctx.fillStyle = "rgba(141, 147, 165, 0.7)";
    this.ctx.fillText("Volume", 140, 11);
    this.ctx.fillText("Delta", 260, 11);
    this.ctx.fillText("dOI", 370, 11);
    this.ctx.fillText("CVD", 470, 11);
    rows.forEach((row, rowIndex) => {
      const top = headerHeight + rowIndex * rowHeight;
      this.ctx.fillStyle = rowIndex % 2 === 0 ? "rgba(255,255,255,0.02)" : "rgba(255,255,255,0.05)";
      this.ctx.fillRect(0, top, width, rowHeight);
      this.ctx.fillStyle = "rgba(255,255,255,0.06)";
      this.ctx.fillRect(0, top, width, 1);
      this.ctx.fillStyle = "rgba(141, 147, 165, 0.92)";
      this.ctx.font = "11px IBM Plex Mono, IBM Plex Sans, monospace";
      this.ctx.fillStyle = row.color || "rgba(141, 147, 165, 0.92)";
      this.ctx.fillText(row.label, 8, top + 17);
    });
    const columns = this.data
      .map((point) => {
        const x = this.chart.timeScale().timeToCoordinate(point.time);
        if (!Number.isFinite(x)) {
          return null;
        }
        const nextX = this.chart.timeScale().timeToCoordinate(point.time + 1);
        const columnWidth = Number.isFinite(nextX) ? Math.max(nextX - x - 1, 6) : 16;
        const computed = this.computeBarStats(point);
        return { point, x, columnWidth, computed };
      })
      .filter(Boolean);
    const populatedBars = columns.filter((column) => Object.values(column.computed || {}).some((value) => Number(value) !== 0)).length;
    this.ctx.fillStyle = "rgba(141, 147, 165, 0.6)";
    this.ctx.fillText(`有值K线: ${populatedBars}/${columns.length}`, 210, 11);
    columns.forEach((column) => {
      this.ctx.strokeStyle = "rgba(255,255,255,0.035)";
      this.ctx.beginPath();
      this.ctx.moveTo(column.x + column.columnWidth / 2, headerHeight);
      this.ctx.lineTo(column.x + column.columnWidth / 2, height);
      this.ctx.stroke();
    });
    columns.forEach(({ point, x, columnWidth, computed }) => {
      rows.forEach((row, rowIndex) => {
        const value = Number(computed?.[row.key] || 0);
        const top = headerHeight + rowIndex * rowHeight + 2;
        const color = row.color || (value >= 0 ? "rgba(111, 142, 207, 0.85)" : "rgba(213, 56, 71, 0.85)");
        const alpha = Math.min(Math.abs(value) / (row.scale || 1), 1);
        this.ctx.fillStyle = color.replace("0.85", String(0.18 + alpha * 0.67));
        this.ctx.fillRect(x - columnWidth / 2, top, columnWidth, rowHeight - 4);
        this.ctx.fillStyle = "rgba(236,240,245,0.88)";
        this.ctx.fillText(row.format ? row.format(value) : `${value}`, x - columnWidth / 2 + 3, top + rowHeight / 2 + 3);
        if (point.time === latestTime) {
          this.ctx.fillStyle = "rgba(255,255,255,0.04)";
          this.ctx.fillRect(x - columnWidth / 2 - 2, top - 1, columnWidth + 4, rowHeight - 2);
          this.ctx.fillStyle = color.replace("0.85", String(0.22 + alpha * 0.72));
          this.ctx.fillRect(x - columnWidth / 2, top, columnWidth, rowHeight - 4);
          this.ctx.fillStyle = "rgba(236,240,245,0.96)";
          this.ctx.fillText(row.format ? row.format(value) : `${value}`, x - columnWidth / 2 + 3, top + rowHeight / 2 + 3);
          this.ctx.strokeStyle = "rgba(255,255,255,0.18)";
          this.ctx.strokeRect(x - columnWidth / 2, top, columnWidth, rowHeight - 4);
        }
      });
    });
    const latestComputed = columns[columns.length - 1]?.computed || {};
    rows.forEach((row, rowIndex) => {
      const latestValue = Number(latestComputed[row.key] || 0);
      const top = headerHeight + rowIndex * rowHeight;
      this.ctx.fillStyle = row.color || "rgba(236,240,245,0.92)";
      this.ctx.fillText(row.format ? row.format(latestValue) : String(latestValue), width - 88, top + 14);
    });
  }

  computeBarStats(point) {
    return computePerBarMicrostructure(point);
  }
}

class UnderBarTextRenderer {
  constructor(paneEntry, definition) {
    this.container = paneEntry.container;
    this.chart = paneEntry.chart;
    this.definition = definition;
    this.data = [];
    this.anchorSeries = this.chart.addLineSeries({
      color: "rgba(0,0,0,0)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    this.canvas = document.createElement("canvas");
    this.canvas.className = "orderflow-label-layer";
    this.container.append(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.dpr = window.devicePixelRatio || 1;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.render());
    this.resize();
  }

  setData(data) {
    this.data = Array.isArray(data) ? data : [];
    this.anchorSeries.setData(this.data.map((point) => ({ time: point.time, value: 0 })));
    this.render();
  }

  setDefinitionOptions(options) {
    this.definition = { ...this.definition, options };
    this.render();
  }

  resize() {
    this.dpr = window.devicePixelRatio || 1;
    const width = Math.max(this.container.clientWidth, 1);
    const height = Math.max(this.container.clientHeight, 1);
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.render();
  }

  destroy() {
    this.resizeObserver.disconnect();
    this.chart.removeSeries(this.anchorSeries);
    this.canvas.remove();
  }

  render() {
    if (!this.ctx) {
      return;
    }
    const width = this.container.clientWidth || 1;
    const height = this.container.clientHeight || 1;
    const rows = this.definition.options?.rows || [];
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, width, height);
    this.ctx.fillStyle = "rgba(16, 17, 22, 0.98)";
    this.ctx.fillRect(0, 0, width, height);
    if (!rows.length || !this.data.length) {
      return;
    }
    const leftLabelWidth = 88;
    const headerHeight = 16;
    const rowGapScale = Math.max(0.75, Math.min(1.6, Number(state.orderflowUi?.barRowGapScale || 1)));
    const availableHeight = Math.max(height - headerHeight, 1);
    const baseRowHeight = availableHeight / rows.length;
    const rowHeight = baseRowHeight * rowGapScale;
    const blockHeight = Math.min(availableHeight, rowHeight * rows.length);
    const blockTop = headerHeight + Math.max((availableHeight - blockHeight) / 2, 0);

    this.ctx.font = "11px IBM Plex Mono, IBM Plex Sans, monospace";
    this.ctx.fillStyle = "rgba(141, 147, 165, 0.84)";
    this.ctx.fillText("Real Trade Orderflow", 8, 11);

    rows.forEach((row, rowIndex) => {
      const top = blockTop + rowIndex * rowHeight;
      this.ctx.fillStyle = rowIndex % 2 === 0 ? "rgba(255,255,255,0.02)" : "rgba(255,255,255,0.05)";
      this.ctx.fillRect(0, top, width, Math.max(rowHeight - 1, 1));
      this.ctx.fillStyle = row.color || "rgba(141, 147, 165, 0.92)";
      this.ctx.fillText(row.label, 8, top + Math.min(rowHeight * 0.72, 15));
      this.ctx.strokeStyle = "rgba(255,255,255,0.05)";
      this.ctx.beginPath();
      this.ctx.moveTo(0, top + 0.5);
      this.ctx.lineTo(width, top + 0.5);
      this.ctx.stroke();
    });

    this.data.forEach((point) => {
      const x = this.chart.timeScale().timeToCoordinate(point.time);
      if (!Number.isFinite(x)) {
        return;
      }
      const nextX = this.chart.timeScale().timeToCoordinate(point.time + 1);
      const columnWidth = Number.isFinite(nextX) ? Math.max(nextX - x - 1, 18) : 28;
      rows.forEach((row, rowIndex) => {
        const top = blockTop + rowIndex * rowHeight;
        const value = Number(point[row.key] || 0);
        const text = row.format ? row.format(value) : String(value);
        this.ctx.fillStyle = row.color || (value >= 0 ? "#69ff7b" : "#ff335f");
        this.ctx.fillText(text, Math.max(leftLabelWidth, x - columnWidth / 2 + 2), top + Math.min(rowHeight * 0.78, 17));
      });
    });
  }
}

function renderBarGrid() {
  if (!els.barGrid) {
    return;
  }
  const candles = (state.seriesDataByKey.get("candles") || []).slice(-12);
  if (candles.length === 0) {
    els.barGrid.innerHTML = "";
    return;
  }

  const header = `
    <div class="bar-grid-row bar-grid-header">
      <div class="bar-grid-cell">Time</div>
      <div class="bar-grid-cell">Delta</div>
      <div class="bar-grid-cell">Speed</div>
      <div class="bar-grid-cell">Efficiency</div>
      <div class="bar-grid-cell">ClosePos</div>
      <div class="bar-grid-cell">HighBuy</div>
      <div class="bar-grid-cell">LowSell</div>
    </div>
  `;

  const rows = candles.map((candle) => {
    const stats = computePerBarMicrostructure(candle);
    const label = (state.timeLabels.get(String(candle.time)) || String(candle.time)).slice(11, 19);
    const deltaClass = stats.delta >= 0 ? "bar-grid-positive" : "bar-grid-negative";
    return `
      <div class="bar-grid-row">
        <div class="bar-grid-cell bar-grid-time">${label}</div>
        <div class="bar-grid-cell ${deltaClass}">${stats.delta.toFixed(3)}</div>
        <div class="bar-grid-cell">${stats.speed.toFixed(3)}</div>
        <div class="bar-grid-cell">${stats.efficiency.toFixed(4)}</div>
        <div class="bar-grid-cell">${stats.close_pos.toFixed(3)}</div>
        <div class="bar-grid-cell">${stats.high_zone_buy_ratio.toFixed(3)}</div>
        <div class="bar-grid-cell">${stats.low_zone_sell_ratio.toFixed(3)}</div>
      </div>
    `;
  }).join("");

  els.barGrid.innerHTML = header + rows;
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
  metaDebug: document.getElementById("meta-debug"),
  barStatsCard: document.querySelector(".bar-stats-card"),
  barflowRowGap: document.getElementById("barflow-row-gap"),
  barflowRowGapValue: document.getElementById("barflow-row-gap-value"),
  barflowBadge: document.getElementById("barflow-badge"),
  barstatDelta: document.getElementById("barstat-delta"),
  barstatSpeed: document.getElementById("barstat-speed"),
  barstatEfficiency: document.getElementById("barstat-efficiency"),
  barstatClosePos: document.getElementById("barstat-close-pos"),
  barstatClosePosFill: document.getElementById("barstat-close-pos-fill"),
  barstatHighBuy: document.getElementById("barstat-high-buy"),
  barstatHighBuyFill: document.getElementById("barstat-high-buy-fill"),
  barstatLowSell: document.getElementById("barstat-low-sell"),
  barstatLowSellFill: document.getElementById("barstat-low-sell-fill"),
  barflowState: document.getElementById("barflow-state"),
  barflowBias: document.getElementById("barflow-bias"),
  barflowTrades: document.getElementById("barflow-trades"),
  barflowVolume: document.getElementById("barflow-volume"),
  barflowCvd: document.getElementById("barflow-cvd"),
  barflowCvdSparkline: document.getElementById("barflow-cvd-sparkline"),
  barflowCvdSparklineLine: document.getElementById("barflow-cvd-sparkline-line"),
  barflowVpPoc: document.getElementById("barflow-vp-poc"),
  barflowVpShare: document.getElementById("barflow-vp-share"),
  barflowVpHvn: document.getElementById("barflow-vp-hvn"),
  barflowVpLvn: document.getElementById("barflow-vp-lvn"),
  barflowBuyRatio: document.getElementById("barflow-buy-ratio"),
  barflowBuyRatioFill: document.getElementById("barflow-buy-ratio-fill"),
  barflowSellRatio: document.getElementById("barflow-sell-ratio"),
  barflowSellRatioFill: document.getElementById("barflow-sell-ratio-fill"),
  barflowDeltaRatio: document.getElementById("barflow-delta-ratio"),
  barGrid: document.getElementById("bar-grid"),
  error: document.getElementById("error-message"),
};

state.timeLabels = new Map();

function syncBarRowGapControl() {
  const value = Math.max(0.75, Math.min(1.6, Number(state.orderflowUi.barRowGapScale || 1)));
  if (els.barflowRowGap) {
    els.barflowRowGap.value = String(value);
  }
  if (els.barflowRowGapValue) {
    els.barflowRowGapValue.textContent = `${value.toFixed(2)}x`;
  }
}

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
  return provider === "binance" && barMode === "time";
}

function wsIntervalForProvider(provider, durationSeconds) {
  const providerIntervals = {
    binance: {
      60: "1m",
      300: "5m",
      900: "15m",
      1800: "30m",
      3600: "1h",
      7200: "2h",
      14400: "4h",
      21600: "6h",
      43200: "12h",
      86400: "1d",
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

function resetOrderflowState() {
  if (state.microstructureFrameId !== null) {
    window.cancelAnimationFrame(state.microstructureFrameId);
    state.microstructureFrameId = null;
  }
  state.orderflowTradeBuckets = new Map();
  state.orderflowBook = { bids: [], asks: [], ts: null };
  state.orderflowRecentTrades = [];
  state.orderflowRecentTradeIds = [];
  state.orderflowSeenTradeIds = new Set();
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
  resetOrderflowState();
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

function orderflowBucketStartMs(timestampMs) {
  const durationMs = getRequestedDuration() * 1000;
  return Math.floor(timestampMs / durationMs) * durationMs;
}

function orderflowPriceKey(price) {
  return Number(price).toFixed(6);
}

function updateOrderflowRendererContexts() {
  state.seriesByKey.forEach((series) => {
    if (typeof series?.setMarketContext === "function") {
      series.setMarketContext({
        candles: state.seriesDataByKey.get("candles") || [],
        timeLabels: state.timeLabels,
        syntheticToActualTime: state.wsSyntheticToActualTime,
        tradeBuckets: state.orderflowTradeBuckets,
        orderBook: state.orderflowBook,
      });
    }
  });
}

function sumBucketDirectionalVolume(bucket) {
  if (!bucket?.levels) {
    return { buyVol: 0, sellVol: 0, totalVol: 0 };
  }
  let buyVol = 0;
  let sellVol = 0;
  let totalVol = 0;
  bucket.levels.forEach((level) => {
    const buy = Number(level?.buy || 0);
    const sell = Number(level?.sell || 0);
    const total = Number(level?.total || buy + sell || 0);
    buyVol += buy;
    sellVol += sell;
    totalVol += total;
  });
  return { buyVol, sellVol, totalVol };
}

function scheduleRealtimeMicrostructureRefresh() {
  if (state.microstructureFrameId !== null) {
    return;
  }
  state.microstructureFrameId = window.requestAnimationFrame(() => {
    state.microstructureFrameId = null;
    updateOrderflowRendererContexts();
    refreshRealtimeBarMicrostats();
    renderMicrostructure();
  });
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

function registerTradeId(tradeId) {
  if (!tradeId) {
    return true;
  }
  if (state.orderflowSeenTradeIds.has(tradeId)) {
    return false;
  }
  state.orderflowSeenTradeIds.add(tradeId);
  state.orderflowRecentTradeIds.push(tradeId);
  while (state.orderflowRecentTradeIds.length > 10000) {
    const removed = state.orderflowRecentTradeIds.shift();
    if (removed) {
      state.orderflowSeenTradeIds.delete(removed);
    }
  }
  return true;
}

function applyBinanceTradeUpdate(rawTrade) {
  const timestampMs = Number(rawTrade.T || rawTrade.E || rawTrade.ts);
  const price = Number(rawTrade.p || rawTrade.price);
  const size = Number(rawTrade.q || rawTrade.size);
  if (!Number.isFinite(timestampMs) || !Number.isFinite(price) || !Number.isFinite(size)) {
    return;
  }
  const tradeId = String(rawTrade.a || rawTrade.id || `${timestampMs}:${price}:${size}`);
  if (!registerTradeId(tradeId)) {
    return;
  }
  const side = rawTrade.m ? "sell" : "buy";
  state.orderflowRecentTrades.push({
    ts: timestampMs,
    price,
    size,
    side,
  });
  const recentCutoff = Date.now() - 10 * 60 * 1000;
  while (state.orderflowRecentTrades.length > 0 && state.orderflowRecentTrades[0].ts < recentCutoff) {
    state.orderflowRecentTrades.shift();
  }
  const bucketStartMs = orderflowBucketStartMs(timestampMs);
  const { syntheticTime } = resolveSyntheticTime(bucketStartMs);
  if (!Number.isFinite(syntheticTime)) {
    return;
  }
  const bucketKey = String(bucketStartMs);
  let bucket = state.orderflowTradeBuckets.get(bucketKey);
  if (!bucket) {
    bucket = { actualTimeMs: bucketStartMs, syntheticTime, levels: new Map(), tradeCount: 0 };
    state.orderflowTradeBuckets.set(bucketKey, bucket);
  } else {
    bucket.syntheticTime = syntheticTime;
  }
  bucket.tradeCount += 1;

  const levelKey = orderflowPriceKey(price);
  const level = bucket.levels.get(levelKey) || { price, buy: 0, sell: 0, total: 0, delta: 0 };
  if (side === "sell") {
    level.sell += size;
    level.delta -= size;
  } else {
    level.buy += size;
    level.delta += size;
  }
  level.total += size;
  bucket.levels.set(levelKey, level);

  const minSyntheticTime = Math.max(0, (state.seriesDataByKey.get("candles") || []).reduce((minValue, item) => {
    const time = Number(item?.time);
    return Number.isFinite(time) ? Math.min(minValue, time) : minValue;
  }, Number.POSITIVE_INFINITY));
  [...state.orderflowTradeBuckets.entries()].forEach(([key, item]) => {
    if (Number.isFinite(minSyntheticTime) && item.syntheticTime < minSyntheticTime - 2) {
      state.orderflowTradeBuckets.delete(key);
    }
  });

  scheduleRealtimeMicrostructureRefresh();
}

function applyBinanceOrderBookSnapshot(book) {
  const normalizeLevels = (levels) =>
    (Array.isArray(levels) ? levels : [])
      .map((level) => {
        const price = Number(level?.[0]);
        const size = Number(level?.[1]);
        if (!Number.isFinite(price) || !Number.isFinite(size)) {
          return null;
        }
        return { price, size };
      })
      .filter(Boolean);
  state.orderflowBook = {
    bids: normalizeLevels(book?.b || book?.bids),
    asks: normalizeLevels(book?.a || book?.asks),
    ts: Number(book?.E || book?.T || Date.now()),
  };
  scheduleRealtimeMicrostructureRefresh();
}

function syncCurrentPriceLine(price, color) {
  const candleSeries = state.seriesByKey.get("candles");
  if (!candleSeries) {
    return;
  }
  if (state.currentPriceLine) {
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

function applyBinanceWsCandleUpdate(rawKline) {
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

  const candles = [...(state.seriesDataByKey.get("candles") || [])];
  const volumeSeriesData = [...(state.seriesDataByKey.get("volume") || [])];
  const nextCandle = { time: syntheticTime, open, high, low, close };
  const nextVolume = {
    time: syntheticTime,
    value: Number.isFinite(volume) ? volume : 0,
    color: close >= open ? "#089981" : "#f23645",
  };
  const existingIndex = candles.findIndex((item) => Number(item?.time) === syntheticTime);
  if (existingIndex >= 0) {
    candles[existingIndex] = nextCandle;
    volumeSeriesData[existingIndex] = nextVolume;
  } else {
    candles.push(nextCandle);
    volumeSeriesData.push(nextVolume);
  }

  const maxLength = currentRequestedDataLength();
  while (candles.length > maxLength) {
    const removed = candles.shift();
    volumeSeriesData.shift();
    if (removed) {
      const removedSynthetic = Number(removed.time);
      const removedActual = state.wsSyntheticToActualTime.get(removedSynthetic);
      if (removedActual !== undefined) {
        state.wsSyntheticToActualTime.delete(removedSynthetic);
        state.wsActualToSyntheticTime.delete(removedActual);
      }
      state.timeLabels.delete(String(removedSynthetic));
    }
  }

  const candleSeries = state.seriesByKey.get("candles");
  const volumeSeries = state.seriesByKey.get("volume");
  setSeriesData("candles", candleSeries, candles);
  setSeriesData("volume", volumeSeries, volumeSeriesData);
  scheduleRealtimeMicrostructureRefresh();

  els.lastPrice.textContent = close.toFixed(2);
  els.lastPrice.style.color = close >= open ? "#089981" : "#f23645";
  els.lastUpdate.textContent = displayTime;
  syncCurrentPriceLine(close, close >= open ? "#089981" : "#f23645");
  updatePaneLabelPositions();

  if (isNewBar) {
    scheduleIndicatorSnapshotSync();
  }
}

function handleBinanceWsMessage(event) {
  if (typeof event.data !== "string" || !event.data) {
    return;
  }
  state.wsLastMessageAt = Date.now();
  const payload = JSON.parse(event.data);
  const stream = String(payload.stream || "").toLowerCase();
  const data = payload.data || {};
  if (stream.includes("@kline_")) {
    applyBinanceWsCandleUpdate(data.k || data);
    return;
  }
  if (stream.includes("@aggtrade")) {
    applyBinanceTradeUpdate(data);
    return;
  }
  if (stream.includes("@depth")) {
    applyBinanceOrderBookSnapshot(data);
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
    symbol: state.config?.symbol || "BTCUSDT",
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
  state.terminalToggles = {
    ...state.terminalToggles,
    ...(nextTemplate.toggles || {}),
  };
  syncToolbarToggles();

  if (els.toolbarProvider) els.toolbarProvider.value = String(nextTemplate.provider || state.config.provider);
  if (els.providerSelect) els.providerSelect.value = String(nextTemplate.provider || state.config.provider);
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

function actualTimeMsForCandle(candle, snapshot = null) {
  const syntheticTime = Number(candle?.time);
  if (!Number.isFinite(syntheticTime)) {
    return null;
  }
  const isTimeMode = (snapshot?.bar_mode || state.activeBarMode) === "time";
  if (isTimeMode) {
    return syntheticTime * 1000;
  }
  const label = snapshot?.time_labels?.[String(syntheticTime)] || state.timeLabels.get(String(syntheticTime));
  if (!label) {
    return null;
  }
  const parsed = Date.parse(label.replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : null;
}

function bucketForCandle(candle, snapshot = null) {
  const actualTimeMs = actualTimeMsForCandle(candle, snapshot);
  if (actualTimeMs === null) {
    return null;
  }
  return state.orderflowTradeBuckets.get(String(actualTimeMs)) || null;
}

function computePerBarMicrostructure(candle, snapshot = null) {
  const open = Number(candle?.open);
  const high = Number(candle?.high);
  const low = Number(candle?.low);
  const close = Number(candle?.close);
  const barSeconds = Math.max(state.activeDurationSeconds || getRequestedDuration() || 60, 1);
  const actualTimeMs = actualTimeMsForCandle(candle, snapshot);
  const bucket = bucketForCandle(candle, snapshot);
  const barEndMs = actualTimeMs === null ? null : actualTimeMs + barSeconds * 1000;
  const trades = !bucket && actualTimeMs !== null
    ? state.orderflowRecentTrades.filter((trade) => trade.ts >= actualTimeMs && trade.ts < barEndMs)
    : [];

  if (![open, high, low, close].every(Number.isFinite)) {
    return { ...EMPTY_MICROSTRUCTURE_STATS };
  }

  if (!bucket && trades.length === 0) {
    return { ...EMPTY_MICROSTRUCTURE_STATS };
  }

  let buyVol = 0;
  let sellVol = 0;
  let highZoneBuy = 0;
  let highZoneTotal = 0;
  let lowZoneSell = 0;
  let lowZoneTotal = 0;
  const range = Math.max(high - low, 1e-9);
  const highZoneThreshold = low + range * (2 / 3);
  const lowZoneThreshold = low + range * (1 / 3);
  if (bucket?.levels) {
    bucket.levels.forEach((level) => {
      const buy = Number(level?.buy || 0);
      const sell = Number(level?.sell || 0);
      const total = Number(level?.total || buy + sell || 0);
      const price = Number(level?.price);
      buyVol += buy;
      sellVol += sell;
      if (price >= highZoneThreshold) {
        highZoneBuy += buy;
        highZoneTotal += total;
      }
      if (price <= lowZoneThreshold) {
        lowZoneSell += sell;
        lowZoneTotal += total;
      }
    });
  } else {
    trades.forEach((trade) => {
      const buy = trade.side === "sell" ? 0 : Number(trade.size || 0);
      const sell = trade.side === "sell" ? Number(trade.size || 0) : 0;
      const total = Number(trade.size || 0);
      const price = Number(trade.price || 0);
      buyVol += buy;
      sellVol += sell;
      if (price >= highZoneThreshold) {
        highZoneBuy += buy;
        highZoneTotal += total;
      }
      if (price <= lowZoneThreshold) {
        lowZoneSell += sell;
        lowZoneTotal += total;
      }
    });
  }
  const totalVol = buyVol + sellVol;
  const imbalanceRatio = (buyVol - sellVol) / (totalVol + 1e-9);
  return {
    delta: buyVol - sellVol,
    speed: Number(bucket?.tradeCount || trades.length || 0) / barSeconds,
    efficiency: Math.abs(close - open) / (range + 1e-9),
    close_pos: (close - low) / (range + 1e-9),
    high_zone_buy_ratio: highZoneBuy / (highZoneTotal + 1e-9),
    low_zone_sell_ratio: lowZoneSell / (lowZoneTotal + 1e-9),
    buy_vol: buyVol,
    sell_vol: sellVol,
    total_vol: totalVol,
    trade_count: Number(bucket?.tradeCount || trades.length || 0),
    buy_ratio: buyVol / (totalVol + 1e-9),
    sell_ratio: sellVol / (totalVol + 1e-9),
    delta_ratio: (buyVol - sellVol) / (totalVol + 1e-9),
    imbalance_ratio: imbalanceRatio,
    dOI: buyVol - sellVol,
  };
}

function classifyRealtimeOrderflow(stats) {
  if (!stats || stats.trade_count <= 0 || stats.total_vol <= 0) {
    return { state: "等待成交", bias: "--", color: "#94a3b8", tone: "neutral" };
  }

  const imbalance = Number(stats.imbalance_ratio || 0);
  const closePos = Number(stats.close_pos || 0);
  const highBuy = Number(stats.high_zone_buy_ratio || 0);
  const lowSell = Number(stats.low_zone_sell_ratio || 0);
  const efficiency = Number(stats.efficiency || 0);
  const speed = Number(stats.speed || 0);
  const deltaRatio = Number(stats.delta_ratio || 0);

  if (imbalance > 0.24 && deltaRatio > 0.22 && closePos > 0.68 && highBuy > 0.60 && efficiency > 0.34) {
    return { state: "多头确认", bias: "偏多", color: "#69ff7b", tone: "bull" };
  }
  if (imbalance > 0.14 && deltaRatio > 0.12 && closePos > 0.58 && highBuy > 0.54 && efficiency > 0.24) {
    return { state: "多头试探", bias: "轻多", color: "#83ff92", tone: "bull" };
  }
  if (imbalance < -0.24 && deltaRatio < -0.22 && closePos < 0.32 && lowSell > 0.60 && efficiency > 0.34) {
    return { state: "空头确认", bias: "偏空", color: "#ff335f", tone: "bear" };
  }
  if (imbalance < -0.14 && deltaRatio < -0.12 && closePos < 0.42 && lowSell > 0.54 && efficiency > 0.24) {
    return { state: "空头试探", bias: "轻空", color: "#ff5f7f", tone: "bear" };
  }
  if (imbalance > 0.14 && closePos < 0.46 && highBuy < 0.52) {
    return { state: "上方吸收确认", bias: "偏空", color: "#ffb347", tone: "bear" };
  }
  if (imbalance < -0.14 && closePos > 0.54 && lowSell < 0.52) {
    return { state: "下方吸收确认", bias: "偏多", color: "#7ad0ff", tone: "bull" };
  }
  if (Math.abs(imbalance) < 0.08 && efficiency < 0.22 && speed > 0.8) {
    return { state: "高速拉锯", bias: "观望", color: "#c9d1df", tone: "neutral" };
  }
  if (Math.abs(imbalance) >= 0.10 || Math.abs(deltaRatio) >= 0.10) {
    return { state: "方向试探", bias: imbalance > 0 ? "轻多" : "轻空", color: "#f5c542", tone: "neutral" };
  }
  return { state: "中性整理", bias: "中性", color: "#c9d1df", tone: "neutral" };
}

function buildRealtimeBarMicrostatsData() {
  const candles = state.seriesDataByKey.get("candles") || [];
  return candles.map((candle) => ({
    time: candle.time,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }));
}

function debugCurrentBarAggregation() {
  const candles = state.seriesDataByKey.get("candles") || [];
  const currentCandle = candles[candles.length - 1];
  if (!currentCandle) {
    return { trades60: 0, barHits: 0, buyVol: 0, sellVol: 0 };
  }
  const bucket = bucketForCandle(currentCandle);
  const trades60 = state.orderflowRecentTrades.filter((trade) => trade.ts >= Date.now() - 60_000).length;
  const { buyVol, sellVol } = sumBucketDirectionalVolume(bucket);
  return { trades60, barHits: Number(bucket?.tradeCount || 0), buyVol, sellVol };
}

function computeVisibleCvd() {
  const candles = state.seriesDataByKey.get("candles") || [];
  if (!candles.length) {
    return { total: 0, series: [] };
  }
  let running = 0;
  const series = candles.map((candle) => {
    const stats = computePerBarMicrostructure(candle);
    running += Number(stats.delta || 0);
    return running;
  });
  return {
    total: running,
    series,
  };
}

function computeSimpleVolumeProfile() {
  const candles = state.seriesDataByKey.get("candles") || [];
  if (!candles.length) {
    return { pocPrice: null, pocShare: 0, hvnPrice: null, lvnPrice: null };
  }

  const profile = new Map();
  let totalVolume = 0;
  candles.forEach((candle) => {
    const bucket = bucketForCandle(candle);
    if (!bucket?.levels) {
      return;
    }
    bucket.levels.forEach((level) => {
      const price = Number(level?.price);
      const total = Number(level?.total || 0);
      if (!Number.isFinite(price) || !Number.isFinite(total) || total <= 0) {
        return;
      }
      totalVolume += total;
      profile.set(price, Number(profile.get(price) || 0) + total);
    });
  });

  if (profile.size === 0 || totalVolume <= 0) {
    return { pocPrice: null, pocShare: 0, hvnPrice: null, lvnPrice: null };
  }

  let pocPrice = null;
  let pocVolume = 0;
  let hvnPrice = null;
  let hvnVolume = -1;
  let lvnPrice = null;
  let lvnVolume = Number.POSITIVE_INFINITY;
  profile.forEach((volume, price) => {
    if (volume > pocVolume) {
      pocPrice = price;
      pocVolume = volume;
    }
    if (volume > hvnVolume) {
      hvnPrice = price;
      hvnVolume = volume;
    }
    if (volume > 0 && volume < lvnVolume) {
      lvnPrice = price;
      lvnVolume = volume;
    }
  });

  let secondaryHvnPrice = null;
  let secondaryHvnVolume = -1;
  profile.forEach((volume, price) => {
    if (price === pocPrice) {
      return;
    }
    if (volume > secondaryHvnVolume) {
      secondaryHvnPrice = price;
      secondaryHvnVolume = volume;
    }
  });

  return {
    pocPrice,
    pocShare: pocVolume / totalVolume,
    hvnPrice: secondaryHvnPrice ?? hvnPrice,
    lvnPrice,
  };
}

function renderCurrentBarStatsCard() {
  const candles = state.seriesDataByKey.get("candles") || [];
  const currentCandle = candles[candles.length - 1];
  const stats = currentCandle ? computePerBarMicrostructure(currentCandle) : null;
  const card = els.barStatsCard;
  const resetCardTone = () => {
    if (!card) {
      return;
    }
    card.classList.remove("is-bull", "is-bear", "is-neutral");
  };
  const setValue = (element, value, digits = 3, color = "") => {
    if (!element) {
      return;
    }
    element.style.color = color || "";
    element.textContent = value === null || value === undefined ? "--" : Number(value).toFixed(digits);
  };
  const setText = (element, value, color = "") => {
    if (!element) {
      return;
    }
    element.style.color = color || "";
    element.textContent = value ?? "--";
  };
  const setMeter = (element, value) => {
    if (!element) {
      return;
    }
    const percent = Math.max(0, Math.min(100, Number(value || 0) * 100));
    element.style.width = `${percent.toFixed(1)}%`;
  };
  const setSparkline = (polyline, values, color = "#7ad0ff") => {
    if (!polyline) {
      return;
    }
    if (!Array.isArray(values) || values.length === 0) {
      polyline.setAttribute("points", "");
      polyline.style.color = color;
      return;
    }
    const width = 120;
    const height = 28;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(max - min, 1e-9);
    const points = values.map((value, index) => {
      const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    polyline.setAttribute("points", points.join(" "));
    polyline.style.color = color;
  };
  if (!stats) {
    resetCardTone();
    setValue(els.barstatDelta, null);
    setValue(els.barstatSpeed, null);
    setValue(els.barstatEfficiency, null, 4);
    setValue(els.barstatClosePos, null);
    setValue(els.barstatHighBuy, null);
    setValue(els.barstatLowSell, null);
    setText(els.barflowState, "--");
    setText(els.barflowBias, "--");
    setText(els.barflowTrades, "--");
    setText(els.barflowVolume, "--");
    setText(els.barflowCvd, "--");
    setText(els.barflowVpPoc, "--");
    setText(els.barflowVpShare, "--");
    setText(els.barflowVpHvn, "--");
    setText(els.barflowVpLvn, "--");
    setText(els.barflowBuyRatio, "--");
    setText(els.barflowSellRatio, "--");
    setText(els.barflowDeltaRatio, "--");
    setText(els.barflowBadge, "等待成交");
    setMeter(els.barflowBuyRatioFill, 0);
    setMeter(els.barflowSellRatioFill, 0);
    setMeter(els.barstatClosePosFill, 0);
    setMeter(els.barstatHighBuyFill, 0);
    setMeter(els.barstatLowSellFill, 0);
    setSparkline(els.barflowCvdSparklineLine, []);
    return;
  }
  const flow = classifyRealtimeOrderflow(stats);
  const visibleCvd = computeVisibleCvd();
  const simpleVp = computeSimpleVolumeProfile();
  resetCardTone();
  if (card) {
    card.classList.add(flow.tone === "bull" ? "is-bull" : flow.tone === "bear" ? "is-bear" : "is-neutral");
  }
  setValue(els.barstatDelta, stats.delta, 3, stats.delta >= 0 ? "#69ff7b" : "#ff335f");
  setValue(els.barstatSpeed, stats.speed, 3, "#7ad0ff");
  setValue(els.barstatEfficiency, stats.efficiency, 3, "#f5c542");
  setValue(els.barstatClosePos, stats.close_pos, 3, "#e5ecf5");
  setValue(els.barstatHighBuy, stats.high_zone_buy_ratio, 3, "#69ff7b");
  setValue(els.barstatLowSell, stats.low_zone_sell_ratio, 3, "#ff335f");
  setText(els.barflowState, flow.state, flow.color);
  setText(els.barflowBias, flow.bias, flow.color);
  setText(els.barflowTrades, String(stats.trade_count || 0), "#e5ecf5");
  setText(els.barflowVolume, Number(stats.total_vol || 0).toFixed(3), "#e5ecf5");
  setText(els.barflowCvd, Number(visibleCvd.total || 0).toFixed(3), visibleCvd.total >= 0 ? "#69ff7b" : "#ff335f");
  setText(
    els.barflowVpPoc,
    Number.isFinite(simpleVp.pocPrice) ? Number(simpleVp.pocPrice).toFixed(2) : "--",
    "#e5ecf5"
  );
  setText(els.barflowVpShare, `${(Number(simpleVp.pocShare || 0) * 100).toFixed(1)}%`, "#f5c542");
  setText(
    els.barflowVpHvn,
    Number.isFinite(simpleVp.hvnPrice) ? Number(simpleVp.hvnPrice).toFixed(2) : "--",
    "#69ff7b"
  );
  setText(
    els.barflowVpLvn,
    Number.isFinite(simpleVp.lvnPrice) ? Number(simpleVp.lvnPrice).toFixed(2) : "--",
    "#ffb347"
  );
  setText(els.barflowBuyRatio, `${(Number(stats.buy_ratio || 0) * 100).toFixed(1)}%`, "#69ff7b");
  setText(els.barflowSellRatio, `${(Number(stats.sell_ratio || 0) * 100).toFixed(1)}%`, "#ff335f");
  setText(els.barflowDeltaRatio, `${Number(stats.delta_ratio || 0).toFixed(3)}`, flow.color);
  setText(els.barflowBadge, flow.state, flow.color);
  setMeter(els.barflowBuyRatioFill, stats.buy_ratio);
  setMeter(els.barflowSellRatioFill, stats.sell_ratio);
  setMeter(els.barstatClosePosFill, stats.close_pos);
  setMeter(els.barstatHighBuyFill, stats.high_zone_buy_ratio);
  setMeter(els.barstatLowSellFill, stats.low_zone_sell_ratio);
  setSparkline(els.barflowCvdSparklineLine, visibleCvd.series, visibleCvd.total >= 0 ? "#69ff7b" : "#ff335f");
}

function renderBarGrid() {
  if (!els.barGrid) {
    return;
  }
  const candles = (state.seriesDataByKey.get("candles") || []).slice(-12);
  if (candles.length === 0) {
    els.barGrid.innerHTML = "";
    return;
  }
  const header = `
    <div class="bar-grid-row bar-grid-header">
      <div class="bar-grid-cell">Time</div>
      <div class="bar-grid-cell">Delta</div>
      <div class="bar-grid-cell">Speed</div>
      <div class="bar-grid-cell">Efficiency</div>
      <div class="bar-grid-cell">ClosePos</div>
      <div class="bar-grid-cell">HighBuy</div>
      <div class="bar-grid-cell">LowSell</div>
    </div>
  `;
  const rows = candles
    .map((candle) => {
      const stats = computePerBarMicrostructure(candle);
      const label = state.timeLabels.get(String(candle.time)) || String(candle.time);
      const deltaCls = stats.delta >= 0 ? "bar-grid-positive" : "bar-grid-negative";
      return `
        <div class="bar-grid-row">
          <div class="bar-grid-cell bar-grid-time">${label.slice(11, 19)}</div>
          <div class="bar-grid-cell ${deltaCls}">${stats.delta.toFixed(3)}</div>
          <div class="bar-grid-cell">${stats.speed.toFixed(3)}</div>
          <div class="bar-grid-cell">${stats.efficiency.toFixed(4)}</div>
          <div class="bar-grid-cell">${stats.close_pos.toFixed(3)}</div>
          <div class="bar-grid-cell">${stats.high_zone_buy_ratio.toFixed(3)}</div>
          <div class="bar-grid-cell">${stats.low_zone_sell_ratio.toFixed(3)}</div>
        </div>
      `;
    })
    .join("");
  els.barGrid.innerHTML = header + rows;
}

function refreshRealtimeBarMicrostats() {
  const statsKey = "indicator:terminal_bar_microstats:terminal_bar_microstats_text";
  const statsSeries = state.seriesByKey.get(statsKey);
  if (!statsSeries) {
    return;
  }
  const candles = buildRealtimeBarMicrostatsData().map((candle) => ({
    time: candle.time,
    ...computePerBarMicrostructure(candle),
  }));
  setSeriesData(statsKey, statsSeries, candles);
}

function renderMicrostructure() {
  if (!els.metaDebug) {
    return;
  }
  const debug = debugCurrentBarAggregation();
  const statsKey = "indicator:terminal_bar_microstats:terminal_bar_microstats_text";
  const statsSeries = state.seriesByKey.get(statsKey);
  let coordText = "x=--";
  if (statsSeries && state.seriesDataByKey.get(statsKey)?.length) {
    const latest = state.seriesDataByKey.get(statsKey).slice(-1)[0];
    const chart = state.seriesChartByKey.get(statsKey);
    const x = chart?.timeScale()?.timeToCoordinate?.(latest.time);
    coordText = Number.isFinite(x) ? `x=${x.toFixed(1)}` : "x=NaN";
  }
  els.metaDebug.textContent = `Trades60=${debug.trades60} / BarHits=${debug.barHits} / Buy=${debug.buyVol.toFixed(3)} / Sell=${debug.sellVol.toFixed(3)} / ${coordText}`;
  renderCurrentBarStatsCard();
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

function augmentTerminalPanels(snapshot) {
  return snapshot;
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
  return points.find((point) => point && point.time === time) || null;
}

function canApplyIncrementalSeriesUpdate(previousData, nextData) {
  if (!Array.isArray(previousData) || !Array.isArray(nextData)) {
    return false;
  }
  if (previousData.length === 0 || nextData.length === 0) {
    return false;
  }
  if (nextData.length < previousData.length) {
    return false;
  }
  if (nextData.length - previousData.length > INCREMENTAL_UPDATE_MAX_NEW_BARS) {
    return false;
  }
  const stablePrefix = previousData.length - 1;
  for (let index = 0; index < stablePrefix; index += 1) {
    if (String(previousData[index]?.time) !== String(nextData[index]?.time)) {
      return false;
    }
  }
  return true;
}

function setSeriesData(seriesKey, series, data) {
  if (typeof series?.setData !== "function") {
    state.seriesDataByKey.set(seriesKey, data);
    return;
  }
  const previousData = state.seriesDataByKey.get(seriesKey);
  if (typeof series?.update === "function" && canApplyIncrementalSeriesUpdate(previousData, data)) {
    const startIndex = Math.max(0, previousData.length - 1);
    data.slice(startIndex).forEach((point) => {
      series.update(point);
    });
  } else {
    series.setData(data);
  }
  state.seriesDataByKey.set(seriesKey, data);
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

function paneHeights(panes) {
  const total = panes.length;
  const hasVolumePane = panes.includes(VOLUME_PANE_ID);
  const hasTerminalStats = panes.includes("terminal_bar_microstats");

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
  if (hasTerminalStats && indicatorCount === 1) {
    return [68, 10, 22];
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
          : paneId === "terminal_bar_microstats"
            ? "150px"
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
  const { fillToSeriesId, fillColor, markers, ...renderOptions } = definition.options || {};
  switch (definition.series_type) {
    case "line":
      return chart.addLineSeries(renderOptions);
    case "histogram":
      return chart.addHistogramSeries(renderOptions);
    case "area":
      return chart.addAreaSeries(renderOptions);
    case "webgl-orderflow":
      return new WebGLOrderflowRenderer(paneEntry, definition);
    case "footprint-stats":
      return new TerminalStatsRenderer(paneEntry, definition);
    case "underbar-text":
      return new UnderBarTextRenderer(paneEntry, definition);
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

function applySnapshot(snapshot) {
  els.error.textContent = "";
  const nextBarMode = snapshot.bar_mode || "time";
  const nextRangeTicks = snapshot.range_ticks || state.config.range_ticks || 10;
  const nextBrickLength = snapshot.brick_length || state.config.brick_length || 10000;
  const shouldRefit =
    snapshot.symbol !== state.activeSymbol ||
    snapshot.duration_seconds !== state.activeDurationSeconds ||
    nextBarMode !== state.activeBarMode ||
    nextRangeTicks !== state.activeRangeTicks ||
    nextBrickLength !== state.activeBrickLength;

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
  const augmentedSnapshot = augmentTerminalPanels(sanitizedSnapshot);
  const trimmedSnapshot = trimSnapshotForDisplay(augmentedSnapshot);
  const displaySnapshot = trimmedSnapshot;
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
  setSeriesData("candles", candleSeries, displaySnapshot.candles);
  setSeriesData("volume", volumeSeries, displaySnapshot.volume);
  candleSeries?.applyOptions({
    upColor: state.terminalToggles.candle ? "#6eff77" : "rgba(0,0,0,0)",
    downColor: state.terminalToggles.candle ? "#ff335f" : "rgba(0,0,0,0)",
    wickUpColor: state.terminalToggles.candle ? "#6eff77" : "rgba(0,0,0,0)",
    wickDownColor: state.terminalToggles.candle ? "#ff335f" : "rgba(0,0,0,0)",
  });
  updateOrderflowRendererContexts();
  const activeBandPrimaryKeys = new Set();

  if (state.currentPriceLine) {
    candleSeries.removePriceLine(state.currentPriceLine);
  }
  state.currentPriceLine = candleSeries.createPriceLine({
    price: snapshot.last_close,
    color: snapshot.last_color,
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title: "现价",
  });

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
    if (displaySnapshot.indicators.length > 0) {
      focusComputedBars(state.charts[0].chart, displaySnapshot);
    } else {
      focusRecentBars(state.charts[0].chart, displaySnapshot.candles.length);
    }
    state.hasFitted = true;
  }

  updatePaneLabelPositions();
  renderMicrostructure();
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
  syncBarRowGapControl();
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
      state.activeSymbol = getRequestedSymbol();
      state.config.symbol = state.activeSymbol;
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
  els.barflowRowGap?.addEventListener("input", () => {
    const value = Number(els.barflowRowGap.value || 1);
    state.orderflowUi.barRowGapScale = Math.max(0.75, Math.min(1.6, value));
    syncBarRowGapControl();
    state.seriesByKey.forEach((series) => {
      if (typeof series?.render === "function") {
        series.render();
      }
    });
  });

  buildIndicatorSelector(state.config.indicators, state.config.default_indicator_ids);
  rebuildCharts();
  renderMicrostructure();
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
