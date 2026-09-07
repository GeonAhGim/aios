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

export function loadBaseline(path) {
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf-8")) : null;
}

export function writeBaseline(path, metrics, meta) {
  const baseline = { ...meta, metrics, updated_at: new Date().toISOString() };
  writeFileSync(path, `${JSON.stringify(baseline, null, 2)}\n`, "utf-8");
  return baseline;
}

/** >20% slower than baseline fails; faster than baseline is reported for the caller to persist. */
export function checkRatchet(current, baselineMetrics, tolerance = REGRESSION_TOLERANCE) {
  const failures = [];
  const improved = {};
  for (const [key, value] of Object.entries(current)) {
    const base = baselineMetrics[key];
    if (typeof base !== "number") continue;
    if (value > base * (1 + tolerance)) {
      failures.push(`${key}: ${value}ms is >${tolerance * 100}% slower than baseline ${base}ms`);
    } else if (value < base) {
      improved[key] = value;
    }
  }
  return { failures, improved };
}
