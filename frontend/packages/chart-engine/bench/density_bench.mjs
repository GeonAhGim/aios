#!/usr/bin/env node
/**
 * CH-19b density bench — extends the CH-0 methodology
 * (packages/ui-web/scripts/bench_chart_candles.mjs: deterministic seeded
 * synthetic OHLCV, percentile helper) to chart-engine's own render pipeline
 * instead of a real browser. It exercises the actual production functions
 * (`cullToViewport`, `downsampleLOD`, `renderPlot`,
 * `createClientIncrementalIndicator`) under the spec's CH-19 density target:
 * 30 indicator instances over 100k candles. Only 10 distinct kernels are
 * verified for client-side compute (CH-18a `verifiedIndicators.ts`), so the
 * 30 instances are modeled as 3 parameter variants per kernel (e.g.
 * SMA(9)/SMA(20)/SMA(50) plotted side by side) — a realistic chart-usage
 * pattern, and honest about what actually runs client-side, unlike
 * fabricating 30 distinct algorithm names.
 *
 * Reports three real measurements. Development-machine targets from the
 * spec row (docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
 * #CH-19) are printed for reference only — pan/zoom frame p95 <= 16.7ms,
 * indicator-add <= 100ms, tick update <= 8ms — and are NEVER asserted here.
 * CI hardware load varies too much for an absolute-ms gate to stay green
 * (decision, repeats the task-1038/1405/920 precedent). The only pass/fail
 * gate is the `density-baseline.json` regression ratchet: any metric more
 * than 20% slower than its baseline fails; a faster metric updates the
 * baseline (same policy as scripts/coverage_ratchet.py, applied per-metric
 * instead of to one rolled-up number).
 *
 * Usage: node --experimental-strip-types bench/density_bench.mjs
 * Exit 0 = no metric regressed >20% vs baseline (or baseline created/improved).
 * Exit 1 = some metric regressed >20% vs baseline.
 */
import { register } from "node:module";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { genCandles, percentile } from "./densityData.mjs";
import { buildCatalog, buildInstances } from "./densityIndicators.mjs";
import { checkRatchet, loadBaseline, writeBaseline } from "./densityRatchet.mjs";

register("./tsLoader.mjs", import.meta.url);

const { createClientIncrementalIndicator } = await import("../src/compute/clientEngine.ts");
const { VERIFIED_KERNEL_PINS } = await import("../src/compute/verifiedIndicators.ts");
const { downsampleLOD } = await import("../src/render/lod.ts");
const { cullToViewport } = await import("../src/render/viewport.ts");
const { renderPlot } = await import("../src/render/plotRenderers.ts");

const HERE = dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = resolvePath(HERE, "density-baseline.json");
const CANDLE_COUNT = 100_000;
const INDICATOR_INSTANCE_COUNT = 30;
const TARGET_PIXEL_WIDTH = 1200;
const PAN_ZOOM_STEPS_PER_PHASE = 60;
const TICK_SAMPLE_COUNT = 500;
const INDICATOR_ADD_RUNS = 5;

/** Feeds the full candle history through every instance, keeping the primary output aligned by candle index (null while unwarmed). */
function warmInstances(instances, candles) {
  const valuesByInstance = new Map();
  for (const inst of instances) {
    const values = new Array(candles.length);
    for (let i = 0; i < candles.length; i++) {
      values[i] = inst.indicator.update(candles[i])[inst.primaryOutput];
    }
    valuesByInstance.set(inst, values);
  }
  return valuesByInstance;
}

/**
 * Strided sample of one instance's values over `[startIndex, endIndex)` —
 * at most `TARGET_PIXEL_WIDTH` points regardless of how wide the window is,
 * same "never draw more vertices than pixels" property `downsampleLOD` gives
 * the candle series. A plain stride (not extrema-preserving bucketing) is
 * enough for a single-value line: unlike an OHLC candle, one line point has
 * no interior wick that a naive stride could hide (see lod.ts's docstring
 * for why candles need the stronger guarantee).
 */
function sampleIndicatorWindow(values, candles, startIndex, endIndex) {
  const span = endIndex - startIndex;
  if (span <= 0) return [];
  const stride = Math.max(1, Math.floor(span / TARGET_PIXEL_WIDTH));
  const points = [];
  for (let i = startIndex; i < endIndex; i += stride) {
    const value = values[i];
    if (value !== null) points.push({ time: candles[i].time, value });
  }
  return points;
}

const NULL_RENDER_TARGET = { drawLine() {}, drawHistogram() {}, drawArea() {}, drawPolygon() {}, drawMarker() {} };
const LINE_STYLE = { output: "primary", color: "#000000", lineWidth: 1, visible: true };

function projectionFor(visibleCandles) {
  let priceLo = Infinity, priceHi = -Infinity, timeLo = Infinity, timeHi = -Infinity;
  for (const c of visibleCandles) {
    if (c.low < priceLo) priceLo = c.low;
    if (c.high > priceHi) priceHi = c.high;
    if (c.time < timeLo) timeLo = c.time;
    if (c.time > timeHi) timeHi = c.time;
  }
  const priceSpan = priceHi - priceLo || 1;
  const timeSpan = timeHi - timeLo || 1;
  return {
    timeToX: (t) => ((t - timeLo) / timeSpan) * TARGET_PIXEL_WIDTH,
    priceToY: (v) => (1 - (v - priceLo) / priceSpan) * 600,
  };
}

/** Zoom phase (shrink from full range to a ~0.2% window) then pan phase (slide that window) — mirrors CH-0's bench. */
function panZoomViewports(candles, stepsPerPhase) {
  const first = candles[0].time;
  const last = candles[candles.length - 1].time;
  const full = last - first;
  const viewports = [];
  for (let i = 0; i < stepsPerPhase; i++) {
    const half = Math.max(full * 0.001, (full / 2) * (1 - i / stepsPerPhase));
    viewports.push({ startTime: first + full / 2 - half, endTime: first + full / 2 + half });
  }
  const windowSpan = full * 0.002;
  for (let i = 0; i < stepsPerPhase; i++) {
    const center = first + windowSpan / 2 + (i / stepsPerPhase) * (full - windowSpan);
    viewports.push({ startTime: center - windowSpan / 2, endTime: center + windowSpan / 2 });
  }
  return viewports;
}

function measurePanZoomFrameMs(candles, instances, valuesByInstance) {
  const viewports = panZoomViewports(candles, PAN_ZOOM_STEPS_PER_PHASE);
  const frameTimes = [];
  for (const viewport of viewports) {
    const t0 = performance.now();
    const culled = cullToViewport(candles, viewport);
    const lod = downsampleLOD(culled.candles.length > 0 ? culled.candles : [candles[0]], TARGET_PIXEL_WIDTH);
    const projection = projectionFor(lod);
    for (const inst of instances) {
      const points = sampleIndicatorWindow(valuesByInstance.get(inst), candles, culled.startIndex, culled.endIndex);
      if (points.length === 0) continue;
      const spec = {
        kind: "line", scale: inst.scale, default_pane: "price",
        fill_between: null, color_rule: null, precision: null, legend_format: null,
      };
      renderPlot(spec, "primary", new Map([["primary", points]]), projection, LINE_STYLE, NULL_RENDER_TARGET);
    }
    frameTimes.push(performance.now() - t0);
  }
  return { p95: percentile(frameTimes, 95), sampleCount: frameTimes.length };
}

/** Cost of backfilling one freshly-added indicator over the whole loaded history. */
function measureIndicatorAddMs(catalog, candles) {
  const runs = [];
  for (let i = 0; i < INDICATOR_ADD_RUNS; i++) {
    const fresh = createClientIncrementalIndicator("SMA", { timeperiod: 20 }, catalog);
    const t0 = performance.now();
    for (const candle of candles) fresh.update(candle);
    runs.push(performance.now() - t0);
  }
  runs.sort((a, b) => a - b);
  return Math.round(runs[Math.floor(runs.length / 2)] * 1000) / 1000;
}

/** Cost of one new live tick propagating through every currently-active indicator instance. */
function measureTickUpdateMs(instances, candles) {
  const lastTime = candles[candles.length - 1].time;
  const times = [];
  for (let i = 0; i < TICK_SAMPLE_COUNT; i++) {
    const tick = { time: lastTime + (i + 1) * 60, open: 100, high: 101, low: 99, close: 100 + (i % 7), volume: 123 };
    const t0 = performance.now();
    for (const inst of instances) inst.indicator.update(tick);
    times.push(performance.now() - t0);
  }
  return { p95: percentile(times, 95), sampleCount: times.length };
}

async function main() {
  console.log(`[density-bench] ${CANDLE_COUNT} candles x ${INDICATOR_INSTANCE_COUNT} indicator instances (10 verified kernels x 3 param variants)`);
  const candles = genCandles(CANDLE_COUNT);
  const catalog = buildCatalog(VERIFIED_KERNEL_PINS);
  const instances = buildInstances(catalog, createClientIncrementalIndicator);
  if (instances.length !== INDICATOR_INSTANCE_COUNT) {
    throw new Error(`expected ${INDICATOR_INSTANCE_COUNT} indicator instances, built ${instances.length}`);
  }
  const valuesByInstance = warmInstances(instances, candles);

  const panZoom = measurePanZoomFrameMs(candles, instances, valuesByInstance);
  const indicatorAddMs = measureIndicatorAddMs(catalog, candles);
  const tickUpdate = measureTickUpdateMs(instances, candles);

  const current = {
    panZoomFrameMsP95: panZoom.p95,
    indicatorAddMs,
    tickUpdateMsP95: tickUpdate.p95,
  };
  console.log("[density-bench] measured:", JSON.stringify(current));
  console.log(
    "[density-bench] dev-machine targets (report only, never asserted in CI): " +
      "panZoomFrameMsP95<=16.7 indicatorAddMs<=100 tickUpdateMsP95<=8",
  );

  const baselineMeta = { candleCount: CANDLE_COUNT, indicatorInstanceCount: INDICATOR_INSTANCE_COUNT };
  const baseline = loadBaseline(BASELINE_PATH);
  if (baseline === null) {
    writeBaseline(BASELINE_PATH, current, baselineMeta);
    console.log(`[density-bench] BASELINE created: ${BASELINE_PATH}`);
    return 0;
  }

  const { failures, improved } = checkRatchet(current, baseline.metrics);
  if (failures.length > 0) {
    console.error("[density-bench] FAIL: regression >20% vs baseline:");
    for (const failure of failures) console.error(`  - ${failure}`);
    return 1;
  }
  if (Object.keys(improved).length > 0) {
    writeBaseline(BASELINE_PATH, { ...baseline.metrics, ...improved }, baselineMeta);
    console.log(`[density-bench] OK: baseline improved: ${JSON.stringify(improved)}`);
  } else {
    console.log("[density-bench] OK: within baseline tolerance");
  }
  return 0;
}

main()
  .then((code) => process.exitCode = code)
  .catch((err) => {
    console.error("[density-bench] error:", err);
    process.exitCode = 1;
  });
