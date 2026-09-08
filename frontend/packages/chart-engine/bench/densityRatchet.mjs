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
 */
export function checkRatchet(current, baselineMetrics, tolerance = REGRESSION_TOLERANCE, floorMs = REGRESSION_FLOOR_MS) {
  const failures = [];
  const improved = {};
  for (const [key, value] of Object.entries(current)) {
    const base = baselineMetrics[key];
    if (typeof base !== "number") continue;
    if (value > base * (1 + tolerance) && value - base > floorMs) {
      failures.push(`${key}: ${value}ms is >${tolerance * 100}% slower than baseline ${base}ms`);
    } else if (value < base) {
      improved[key] = value;
    }
  }
  return { failures, improved };
}
