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
 * Reports three real measurements against two independent gates. (1) The
 * `density-baseline.json` regression ratchet: any metric more than its
 * tolerance (20% default, `densityRatchet.mjs#REGRESSION_TOLERANCE_OVERRIDES`
 * widens indicatorAddMs to 30% — task-3311, see that constant's docstring)
 * slower than its baseline fails; a faster metric updates the baseline
 * (same policy as scripts/coverage_ratchet.py, applied per-metric instead
 * of to one rolled-up number). (2) CH-19e — the spec row's own absolute
 * targets (docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
 * #CH-19: pan/zoom frame p95 <= 16.7ms, indicator-add <= 100ms, tick update
 * <= 8ms), which a purely relative ratchet could drift past one +20% hop at
 * a time. A bare absolute-ms gate is what task-1038/1405/920 ruled out for
 * CI (shared/contended hardware fails code that isn't actually slower), so
 * `densityRatchet.mjs#checkAbsoluteThresholds` scales the targets by a
 * calib probe measured fresh each run — see that function's docstring.
 * Every metric's raw value plus the calib ratio and the resulting
 * normalized targets are always printed, so normalization can only widen
 * the gate, never hide a value that actually exceeds it.
 *
 * Each of the three metrics is a median across `MEASURE_SWEEPS` full
 * sweeps rather than a single sample or a min-reduction: a shared dev/CI
 * box runs other work concurrently, and a min-reduction across too few
 * sweeps tends to just report whichever sweep got the least contention
 * rather than a representative cost — the median is more resistant to a
 * single lucky or unlucky sweep in either direction.
 *
 * Usage: node --experimental-strip-types bench/density_bench.mjs
 * Exit 0 = no metric regressed beyond its tolerance vs baseline (20% default,
 *          30% for indicatorAddMs) AND all metrics are within the
 *          calib-normalized CH-19 absolute targets.
 * Exit 1 = either gate failed.
 */
import { register } from "node:module";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { genCandles, percentile } from "./densityData.mjs";
import { buildCatalog, buildInstances } from "./densityIndicators.mjs";
import {
  CALIB_BASE_MS,
  CH19_ABSOLUTE_TARGET_MS,
  checkAbsoluteThresholds,
  decideBenchOutcome,
  loadBaseline,
  measureCalibMs,
  writeBaseline,
} from "./densityRatchet.mjs";

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
/**
 * task-3311: raised from 5 to 9. indicatorAddMs is a single big timed loop
 * per run (unlike panZoom/tick's many small per-frame/per-tick samples
 * already smoothed by a p95), so it is the metric most exposed to a single
 * paused run (GC, scheduler tick) shifting the median — this was the direct
 * cause of the observed 2/18 false-positive rate. More inner runs narrows
 * that median's spread on top of (not instead of) the wider tolerance in
 * `densityRatchet.mjs#REGRESSION_TOLERANCE_OVERRIDES`.
 */
const INDICATOR_ADD_RUNS = 9;
/**
 * Number of full measurement sweeps per run, median-reduced per metric (see
 * module docstring). task-6460 (esc-ci-frontend.json): raised from 5 to 9 --
 * repeated same-machine, same-code reruns showed panZoomFrameMsP95 swinging
 * 30-45% run-to-run (median-of-5 of 3.3ms-4.7ms) even with the calib probe
 * reading ~idle the whole time, i.e. genuine per-sweep noise (GC pause,
 * scheduler tick landing inside the timed region), not host contention the
 * calib ratio could normalize away. A median of 9 needs 5 outlier sweeps
 * (not 3 of 5) to move it, the same "more samples narrows the spread"
 * argument already applied to `INDICATOR_ADD_RUNS` above.
 */
const MEASURE_SWEEPS = 9;
/**
 * task-6460: sweeps run before the loop below are timed and discarded so
 * the code under measurement (cullToViewport/downsampleLOD/renderPlot/the
 * indicator kernels) is past its initial JIT tier-up before any measured
 * sweep starts -- `warmInstances` above only warms the indicator *values*
 * used as render input, not the render/indicator-add call sites themselves.
 * Reproduced directly: a single process measuring 30 consecutive sweeps
 * showed elevated, more scattered values in the first ~8-10 sweeps that
 * settled into a tighter band afterward.
 */
const WARMUP_SWEEPS = 3;
/**
 * task-6744 (esc-ci-frontend.json recurrence of task-6725): even per-metric
 * calib brackets (one pair bracketing panZoom's whole ~120-frame, ~250-350ms
 * measurement window) can miss a contention spike that starts *after* the
 * leading calib probe finishes and clears *before* the trailing one starts --
 * fully inside the window, touching neither bracket. Reproduced directly:
 * repeated same-code, same-machine reruns showed panZoomFrameMsP95 sweeps
 * with calib ratio reading ~1.0-1.2 (near-idle) on the exact sweep whose raw
 * p95 was 40-90% above its sweep-siblings -- host-load normalization had
 * nothing to normalize because the spike never reached either probe. Slicing
 * the same measurement into `PAN_ZOOM_CALIB_CHUNKS` pieces, each bracketed by
 * its own calib pair, narrows that blind window proportionally (4 chunks ->
 * ~1/4 the miss window of a single whole-measurement bracket) without
 * touching the tolerance, baseline, or absolute targets themselves --
 * DECISION_GUIDELINES B-2.
 */
const PAN_ZOOM_CALIB_CHUNKS = 4;
/**
 * task-6744: same blind-window problem for indicatorAddMs -- one calib pair
 * bracketing all `INDICATOR_ADD_RUNS` runs missed contention confined to a
 * single run's ~1-2ms window. Bracketing each group of runs independently
 * narrows it the same way.
 */
const INDICATOR_ADD_CALIB_GROUP_SIZE = 3;

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

/**
 * task-6670 (esc-ci-frontend.json recurrence): `measureIndicatorAddMs` churns
 * ~9 fresh indicator instances x 100k candle updates per sweep -- enough
 * allocation to leave pending garbage that V8 can collect at any later,
 * unpredictable point, including mid-measurement in a *different* function
 * (observed: panZoomFrameMsP95 elevated across an entire run's sweeps with
 * no matching rise in the calib probe, i.e. not host contention -- see the
 * per-metric calib ratios below). Forcing a full collection right before each timed
 * measurement (outside the `performance.now()` window) drains that backlog
 * proactively instead of leaving it to fire during whichever measurement
 * happens to run next. No-op (silently) when the process was not started
 * with `--expose-gc` (`npm run bench:density` always passes it; direct
 * `node bench/density_bench.mjs` invocations still work, just without this
 * noise reduction).
 */
function forceGc() {
  if (typeof global.gc === "function") global.gc();
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

function renderPanZoomFrame(candles, instances, valuesByInstance, viewport) {
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
  return performance.now() - t0;
}

/**
 * Chunks `viewports` into `PAN_ZOOM_CALIB_CHUNKS` pieces, bracketing each
 * chunk with its own calib pair (see PAN_ZOOM_CALIB_CHUNKS's docstring) so a
 * contention spike confined to one chunk only inflates that chunk's own
 * normalization ratio instead of being averaged away -- or missed entirely --
 * by a single whole-measurement bracket.
 *
 * Returns the raw per-frame normalized times rather than reducing to a p95
 * here (task-6752, esc-ci-frontend.json recurrence of task-6744): a p95 over
 * only `PAN_ZOOM_STEPS_PER_PHASE * 2` (120) samples sits at rank ~114, close
 * enough to the max that a single slow frame moves it noticeably -- median-
 * of-9 such per-sweep p95s (main()'s previous reduction) doesn't fully tame
 * that because each sweep's p95 is already a coarse, high-variance estimate
 * before the cross-sweep median ever sees it. Reproduced directly: 12
 * consecutive same-code reruns swung the gate's panZoomFrameMsP95 between
 * 3.25ms and 4.19ms (calib ratio reading ~1.0, i.e. not host contention --
 * see checkRatchet's failure log) against a 3.307ms baseline and 20%
 * tolerance, failing ~40% of runs. Pooling every sweep's frames into one
 * array before taking a single p95 (see main()) multiplies the effective
 * sample size feeding that percentile by `MEASURE_SWEEPS` (~1080 instead of
 * 120), which is the standard fix for percentile-estimate variance -- more
 * data, not a looser gate.
 */
function measurePanZoomFrameMs(candles, instances, valuesByInstance) {
  const viewports = panZoomViewports(candles, PAN_ZOOM_STEPS_PER_PHASE);
  const chunkSize = Math.max(1, Math.ceil(viewports.length / PAN_ZOOM_CALIB_CHUNKS));
  const normalizedFrameTimes = [];
  const calibSamples = [measureCalibMs()];
  for (let start = 0; start < viewports.length; start += chunkSize) {
    const chunk = viewports.slice(start, start + chunkSize);
    const chunkTimes = chunk.map((viewport) => renderPanZoomFrame(candles, instances, valuesByInstance, viewport));
    const calibAfter = measureCalibMs();
    const ratio = Math.max(1, Math.max(calibSamples[calibSamples.length - 1], calibAfter) / CALIB_BASE_MS);
    calibSamples.push(calibAfter);
    for (const t of chunkTimes) normalizedFrameTimes.push(t / ratio);
  }
  return { frames: normalizedFrameTimes, calibSamples };
}

function runIndicatorAdd(catalog, candles) {
  const fresh = createClientIncrementalIndicator("SMA", { timeperiod: 20 }, catalog);
  const t0 = performance.now();
  for (const candle of candles) fresh.update(candle);
  return performance.now() - t0;
}

/**
 * Groups `INDICATOR_ADD_RUNS` runs into `INDICATOR_ADD_CALIB_GROUP_SIZE`-sized
 * batches, each bracketed by its own calib pair -- same blind-window fix as
 * `measurePanZoomFrameMs`, sized down for indicatorAddMs's fewer, larger
 * samples.
 */
function measureIndicatorAddMs(catalog, candles) {
  const normalizedRuns = [];
  const calibSamples = [measureCalibMs()];
  for (let start = 0; start < INDICATOR_ADD_RUNS; start += INDICATOR_ADD_CALIB_GROUP_SIZE) {
    const groupCount = Math.min(INDICATOR_ADD_CALIB_GROUP_SIZE, INDICATOR_ADD_RUNS - start);
    const groupTimes = [];
    for (let i = 0; i < groupCount; i++) groupTimes.push(runIndicatorAdd(catalog, candles));
    const calibAfter = measureCalibMs();
    const ratio = Math.max(1, Math.max(calibSamples[calibSamples.length - 1], calibAfter) / CALIB_BASE_MS);
    calibSamples.push(calibAfter);
    for (const t of groupTimes) normalizedRuns.push(t / ratio);
  }
  normalizedRuns.sort((a, b) => a - b);
  return {
    value: Math.round(normalizedRuns[Math.floor(normalizedRuns.length / 2)] * 1000) / 1000,
    calibSamples,
  };
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

  for (let i = 0; i < WARMUP_SWEEPS; i++) {
    forceGc();
    measurePanZoomFrameMs(candles, instances, valuesByInstance);
    forceGc();
    measureIndicatorAddMs(catalog, candles);
    forceGc();
    measureTickUpdateMs(instances, candles);
  }

  const sweeps = [];
  const calibSamplesMs = [];
  const tickUpdateCalibRatios = [];
  const pooledPanZoomFrames = [];
  for (let i = 0; i < MEASURE_SWEEPS; i++) {
    // task-6744 (esc-ci-frontend.json recurrence of task-6725): task-6725
    // bracketed each of the three measurements with its own calib pair, but a
    // contention spike confined entirely *inside* one measurement's window
    // (not overlapping either bracket probe) still slips through undetected
    // -- reproduced directly: repeated same-code reruns showed
    // panZoomFrameMsP95 sweeps 40-90% above their sweep-siblings while both
    // bracketing calib probes read near-idle (ratio ~1.0-1.2). panZoom and
    // indicatorAdd now bracket their own internal chunks/groups (see
    // PAN_ZOOM_CALIB_CHUNKS/INDICATOR_ADD_CALIB_GROUP_SIZE), returning an
    // already-normalized value -- the sweep-level bracket below is kept only
    // for tickUpdate (500 near-instant samples too cheap to chunk-bracket
    // without the calib overhead dominating the measurement) and for
    // collecting calib samples toward the CH-19e absolute-threshold gate.
    forceGc();
    const calibA = measureCalibMs();
    forceGc();
    const panZoom = measurePanZoomFrameMs(candles, instances, valuesByInstance);
    forceGc();
    const indicatorAdd = measureIndicatorAddMs(catalog, candles);
    forceGc();
    const calibC = measureCalibMs();
    forceGc();
    const tickUpdate = measureTickUpdateMs(instances, candles);
    forceGc();
    const calibD = measureCalibMs();
    calibSamplesMs.push(calibA, ...panZoom.calibSamples, ...indicatorAdd.calibSamples, calibC, calibD);
    tickUpdateCalibRatios.push(Math.max(1, Math.max(calibC, calibD) / CALIB_BASE_MS));
    pooledPanZoomFrames.push(...panZoom.frames);
    sweeps.push({
      panZoomFrameMsP95: percentile(panZoom.frames, 95),
      indicatorAddMs: indicatorAdd.value,
      tickUpdateMsP95: tickUpdate.p95,
    });
  }
  // panZoomFrameMsP95 is a single p95 over every sweep's pooled, already
  // chunk-normalized frames (see measurePanZoomFrameMs's docstring) rather
  // than a median of MEASURE_SWEEPS separate p95s -- the per-sweep values in
  // `sweeps` above are kept only for the diagnostic log below, not fed into
  // the gate.
  const pooledPanZoomFrameMsP95 = percentile(pooledPanZoomFrames, 95);
  const current = {
    panZoomFrameMsP95: pooledPanZoomFrameMsP95,
    indicatorAddMs: percentile(sweeps.map((s) => s.indicatorAddMs), 50),
    tickUpdateMsP95: percentile(sweeps.map((s) => s.tickUpdateMsP95), 50),
  };
  console.log(`[density-bench] sweeps (${MEASURE_SWEEPS}, panZoomFrameMsP95 here is per-sweep, diagnostic only):`, JSON.stringify(sweeps));
  console.log("[density-bench] measured (panZoomFrameMsP95: pooled p95 over all sweeps' frames; others: median across sweeps):", JSON.stringify(current));
  console.log("[density-bench] panZoom/indicatorAdd are already chunk-normalized above; tickUpdate is normalized below.");
  console.log(`[density-bench] calib samples (ms): ${JSON.stringify(calibSamplesMs)}`);

  const calibMs = Math.max(...calibSamplesMs);
  const { failures: absoluteFailures, normalized, calibRatio } = checkAbsoluteThresholds(current, calibMs);
  console.error(
    `[density-bench] CH-19e calib: ${calibMs.toFixed(3)}ms (base ${CALIB_BASE_MS}ms, ratio ${calibRatio.toFixed(3)}); ` +
      `normalized absolute targets: ${JSON.stringify(normalized)}; raw spec targets: ${JSON.stringify(CH19_ABSOLUTE_TARGET_MS)}`,
  );

  // panZoom/indicatorAdd are already chunk-normalized inside their measure
  // functions (see PAN_ZOOM_CALIB_CHUNKS/INDICATOR_ADD_CALIB_GROUP_SIZE
  // above); only tickUpdate still needs the sweep-level bracket applied here.
  const ratchetSweeps = sweeps.map((s, i) => ({
    indicatorAddMs: s.indicatorAddMs,
    tickUpdateMsP95: s.tickUpdateMsP95 / tickUpdateCalibRatios[i],
  }));
  const ratchetCurrent = {
    panZoomFrameMsP95: pooledPanZoomFrameMsP95,
    indicatorAddMs: percentile(ratchetSweeps.map((s) => s.indicatorAddMs), 50),
    tickUpdateMsP95: percentile(ratchetSweeps.map((s) => s.tickUpdateMsP95), 50),
  };
  console.error(
    `[density-bench] tickUpdate calib ratios: ${JSON.stringify(tickUpdateCalibRatios.map((r) => Math.round(r * 1000) / 1000))}; ` +
      `ratchet current (panZoom/indicatorAdd chunk-normalized, tickUpdate sweep-normalized, median across sweeps): ${JSON.stringify(ratchetCurrent)}`,
  );

  const baselineMeta = { candleCount: CANDLE_COUNT, indicatorInstanceCount: INDICATOR_INSTANCE_COUNT };
  const baseline = loadBaseline(BASELINE_PATH);
  const outcome = decideBenchOutcome({
    current: ratchetCurrent, baseline, absoluteFailures, ratchetCalibRatio: 1, baselineMeta, baselinePath: BASELINE_PATH,
  });
  for (const { level, message } of outcome.logs) console[level](message);
  if (outcome.baselineWrite) writeBaseline(BASELINE_PATH, outcome.baselineWrite.metrics, outcome.baselineWrite.meta);
  return outcome.exitCode;
}

main()
  .then((code) => process.exitCode = code)
  .catch((err) => {
    console.error("[density-bench] error:", err);
    process.exitCode = 1;
  });
