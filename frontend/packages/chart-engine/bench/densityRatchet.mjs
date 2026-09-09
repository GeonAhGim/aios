/**
 * CH-19b — per-metric regression ratchet for `density_bench.mjs`, split out
 * so the bench file itself stays under the 300-line budget. Same policy as
 * scripts/coverage_ratchet.py/frontend_coverage_ratchet.mjs, applied to each
 * metric independently instead of to one rolled-up number: a metric more
 * than `tolerance` slower than its baseline fails; a faster metric updates
 * the baseline. No absolute threshold is ever asserted (see density_bench.mjs
 * docstring for why).
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";

export const REGRESSION_TOLERANCE = 0.2;

/**
 * Below this absolute gap, a percentage comparison is meaningless: e.g.
 * tickUpdateMsP95 sits around 0.01-0.03ms on a quiet machine, where a single
 * OS scheduler tick (commonly 10s of microseconds) swings the percentage by
 * 100%+ without the code getting any slower. Requiring the gap to also clear
 * this floor keeps the ratchet meaningful at sub-millisecond scale instead of
 * amplifying timer/scheduler noise into a false regression.
 */
export const REGRESSION_FLOOR_MS = 0.05;

export function loadBaseline(path) {
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf-8")) : null;
}

export function writeBaseline(path, metrics, meta) {
  const baseline = { ...meta, metrics, updated_at: new Date().toISOString() };
  writeFileSync(path, `${JSON.stringify(baseline, null, 2)}\n`, "utf-8");
  return baseline;
}

/**
 * >20% slower than baseline AND more than REGRESSION_FLOOR_MS slower in
 * absolute terms fails; faster than baseline is reported for the caller to
 * persist.
 *
 * `calibRatio` (see `checkAbsoluteThresholds` below) brings `value` back to
 * the reference host's time scale before comparing: without it, a
 * shared/contended box that is merely slower right now — not a real
 * regression — fails every metric uniformly (task-2479: observed
 * panZoomFrameMsP95/indicatorAddMs/tickUpdateMsP95 all ~6-13x over baseline
 * on a run whose calib probe itself was ~20x the reference cost). `improved`
 * stores the normalized value too, so the persisted baseline always stays in
 * reference-host-equivalent terms regardless of which host recorded it.
 */
export function checkRatchet(current, baselineMetrics, calibRatio = 1, tolerance = REGRESSION_TOLERANCE, floorMs = REGRESSION_FLOOR_MS) {
  const failures = [];
  const improved = {};
  for (const [key, value] of Object.entries(current)) {
    const base = baselineMetrics[key];
    if (typeof base !== "number") continue;
    const normalized = value / calibRatio;
    if (normalized > base * (1 + tolerance) && normalized - base > floorMs) {
      failures.push(
        `${key}: ${value}ms (host-load normalized ${normalized.toFixed(3)}ms @ calib ratio ${calibRatio.toFixed(3)}) is >${tolerance * 100}% slower than baseline ${base}ms`,
      );
    } else if (normalized < base) {
      improved[key] = normalized;
    }
  }
  return { failures, improved };
}

/**
 * CH-19e — spec (§9.11 CH-19) absolute thresholds, host-load normalized.
 * These sit alongside checkRatchet's baseline ratchet (never replace it):
 * the ratchet catches the bench's own code regressing; this catches the
 * bench drifting away from what the spec actually promises users, which a
 * pure relative ratchet can silently drift past one +20% hop at a time.
 *
 * A bare absolute ms gate is exactly what task-1038/1405/920 (see this
 * file's density_bench.mjs docstring) ruled out for CI: a shared/contended
 * machine fails code that is not actually slower. So the target is scaled
 * by how loaded *this run's* host is, measured directly instead of assumed:
 * `measureCalibMs()` times a fixed, side-effect-free synthetic workload
 * right before the real measurements, and `calibRatio = calibMs /
 * CALIB_BASE_MS` says how many times slower this run's host is than the
 * reference. `max(1, ratio)` never *tightens* the target below spec on a
 * fast host — CH-19 is already the floor.
 */
export const CH19_ABSOLUTE_TARGET_MS = {
  panZoomFrameMsP95: 16.7,
  indicatorAddMs: 100,
  tickUpdateMsP95: 8,
};

const CALIB_SAMPLES = 5;
const CALIB_INNER_ITERATIONS = 300;
const CALIB_BUFFER_SIZE = 4096;

/**
 * Reference calib probe cost for CH19_ABSOLUTE_TARGET_MS's "1.0x" host.
 * Measured 2026-09-09 on this session's machine (git 370d6507) with no
 * other benches running: 5 calls to measureCalibMs(), each itself a
 * median of 5 inner samples: 15.86/13.13/12.67/12.27/14.00ms, median of
 * medians 13.13ms. Rounded down slightly so a rerun on the same idle
 * machine reads as ratio<=1 (raw target, unscaled) rather than drifting
 * >1 from measurement jitter alone.
 */
export const CALIB_BASE_MS = 13.0;

/**
 * Fixed-iteration float/typed-array workload with no dependency on chart
 * code, candle data, or indicator kernels — its only job is to answer "how
 * fast is this CPU right now", independent of anything under test. `acc`
 * is folded into a never-thrown check purely so the JIT can't prove the
 * loop body is dead and elide it.
 */
export function measureCalibMs() {
  const times = [];
  for (let s = 0; s < CALIB_SAMPLES; s++) {
    const buf = new Float64Array(CALIB_BUFFER_SIZE);
    const t0 = performance.now();
    let acc = 0;
    for (let iter = 0; iter < CALIB_INNER_ITERATIONS; iter++) {
      for (let i = 0; i < buf.length; i++) {
        buf[i] = Math.sin(i * 0.001 + iter) * Math.sqrt(i + 1);
        acc += buf[i];
      }
    }
    const elapsed = performance.now() - t0;
    if (!Number.isFinite(acc)) throw new Error("unreachable: calib probe produced a non-finite accumulator");
    times.push(elapsed);
  }
  times.sort((a, b) => a - b);
  return times[Math.floor(times.length / 2)];
}

/**
 * calibMs/calibBaseMs (floored at 1) scales every target in `targets` up
 * for a loaded host; `normalized` and `calibRatio` are returned (not just
 * failures) so the caller can print them unconditionally — normalization
 * must never be able to hide a real regression silently.
 */
export function checkAbsoluteThresholds(current, calibMs, calibBaseMs = CALIB_BASE_MS, targets = CH19_ABSOLUTE_TARGET_MS) {
  const calibRatio = Math.max(1, calibMs / calibBaseMs);
  const normalized = {};
  const failures = [];
  for (const [key, target] of Object.entries(targets)) {
    const normalizedTarget = target * calibRatio;
    normalized[key] = normalizedTarget;
    const value = current[key];
    if (typeof value === "number" && value > normalizedTarget) {
      failures.push(
        `${key} ${value}ms exceeds normalized target ${normalizedTarget.toFixed(3)}ms ` +
          `(spec target ${target}ms x calib ratio ${calibRatio.toFixed(3)})`,
      );
    }
  }
  return { failures, normalized, calibRatio };
}
