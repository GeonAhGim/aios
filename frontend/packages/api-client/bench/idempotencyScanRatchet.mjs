/**
 * task-4973 — ratio-to-calib ratchet for `idempotencyScan_bench.mjs`, split
 * out so it can be unit-tested (decideBenchOutcome) without the real
 * clients/*.ts scan or a TypeScript compiler program.
 *
 * Unlike packages/chart-engine/bench/densityRatchet.mjs this asserts no
 * absolute millisecond figure at all (not even host-load normalized) and
 * keeps no persisted baseline file — the scan this gate covers is a single
 * cheap measurement (tens of ms in isolation), not a multi-metric render
 * pipeline, so a baseline-drift ratchet would be more machinery than the
 * signal warrants. Instead: measure a fixed, side-effect-free calib
 * workload in the same process right before the real scan, then require
 * `scanMs <= RATIO_MULTIPLIER * calibMs`. Both numbers come from the same
 * run on the same (possibly contended) host, so host load cancels out of
 * the ratio — this is exactly what made the old fixed-200ms/1000ms
 * wall-clock assertion in idempotencyScan.test.ts flaky under
 * `npm run test --workspaces` (task-4968/a22cdb09: 5 workspaces' vitest
 * worker threads compete for the same CPU, and ts.createSourceFile's
 * JIT/GC responds non-linearly to that contention). A real algorithmic
 * regression in findCallSites/scanCallSites does not make the calib
 * workload proportionally slower, so it still trips this gate.
 */

/**
 * How many multiples of the calib workload's cost the real scan may take.
 * Calibrated 2026-09-22 on this session's dev machine (git 0c3973ca) with
 * `node --experimental-strip-types bench/idempotencyScan_bench.mjs`
 * (30 clients/*.ts files): idle, ratio ~3.3x (calibMs ~40ms, scanMs
 * ~130ms); with 5 concurrent copies of this same bench racing for CPU,
 * ratio ~3.1-3.9x (calibMs ~45-59ms, scanMs ~170-183ms); with 10
 * concurrent copies (deliberately harsher than the 5-workspace contention
 * task-4968 observed causing the old fixed-ms assertion to fail at
 * 271-550ms), ratio still ~2.96-3.95x (calibMs ~62-71ms, scanMs
 * ~209-256ms) -- the calib probe absorbs host load at essentially the same
 * rate as the real scan, so the ratio stays flat while the absolute
 * numbers both roughly double. 12x leaves >3x headroom over the worst
 * observed ratio, while still catching an order-of-magnitude algorithmic
 * regression (e.g. an accidental O(n^2) rewrite of findCallSites).
 */
export const RATIO_MULTIPLIER = 12;

const CALIB_SAMPLES = 5;
/**
 * task-4973: sized so calibMs lands in the same order of magnitude as the
 * ~10-15ms isolated scanMs (measured on this session's dev machine,
 * 2026-09-22) instead of the sub-2ms a smaller workload produced during
 * calibration -- a probe that fast is dominated by single-GC-pause jitter
 * (observed +-30% run to run), which the division in decideBenchOutcome
 * then amplifies into an even noisier ratio. A calib probe closer in scale
 * to the thing it normalizes stays proportionally stable under the same
 * host contention that inflates the real scan.
 */
const CALIB_INNER_ITERATIONS = 2500;
const CALIB_BUFFER_SIZE = 2048;

/**
 * Fixed-iteration float/typed-array workload with no dependency on the
 * scanner, clients/*.ts, or the TypeScript compiler -- its only job is to
 * answer "how loaded is this host right now", independent of anything
 * under test. `acc` is folded into a never-thrown check purely so the JIT
 * can't prove the loop body is dead and elide it.
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
 * Pure decision function (no fs/console side effects) so it can be tested
 * against fabricated scanMs/calibMs pairs instead of the real 47-file scan.
 */
export function decideBenchOutcome(scanMs, calibMs, ratioMultiplier = RATIO_MULTIPLIER) {
  const ratio = calibMs > 0 ? scanMs / calibMs : Infinity;
  const budgetMs = calibMs * ratioMultiplier;
  const ok = scanMs <= budgetMs;
  const message = ok
    ? `[idempotency-scan-bench] OK: scanMs=${scanMs.toFixed(3)} calibMs=${calibMs.toFixed(3)} ratio=${ratio.toFixed(2)}x (budget ${ratioMultiplier}x, ${budgetMs.toFixed(3)}ms)`
    : `[idempotency-scan-bench] FAIL: scanMs=${scanMs.toFixed(3)} exceeds ${ratioMultiplier}x calibMs=${calibMs.toFixed(3)} (budget ${budgetMs.toFixed(3)}ms, actual ratio ${ratio.toFixed(2)}x)`;
  return { ok, ratio, budgetMs, exitCode: ok ? 0 : 1, message };
}
