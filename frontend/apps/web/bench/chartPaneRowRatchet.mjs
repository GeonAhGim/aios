/**
 * task-5419 -- ratio-to-calib ratchet for `chartPaneRow_bench.mjs`, mirroring
 * packages/api-client/bench/idempotencyScanRatchet.mjs (task-4973) exactly:
 * split out so `decideBenchOutcome`/`measureCalibMs` are unit-testable
 * without spinning up Vite SSR + jsdom + a real ChartPaneRow render.
 *
 * Same rationale as the api-client precedent applies here almost verbatim:
 * the wall-clock assertion this bench replaces
 * (ChartPaneRow.test.tsx's "고밀도 렌더: ... 100ms 내로 렌더된다", Actions run
 * 35795467536/f0fbb4ba, task-5419) failed on the shared GitHub-hosted runner
 * at 348.2ms despite no functional regression -- a fixed-ms budget in a unit
 * test can't distinguish "the component got slower" from "the runner is
 * contended right now". Measuring a fixed, side-effect-free calib workload
 * in the same process immediately around the real render and gating on
 * `renderMs <= RATIO_MULTIPLIER * calibMs` cancels host load out of the
 * ratio instead of trying to out-guess it with a bigger constant.
 */

/**
 * How many multiples of the calib workload's cost the render may take.
 * Calibrated 2026-09-23 on this session's dev machine with
 * `npm run bench:chart-pane-row --workspace=apps/web` (1000-node SVG render
 * of ChartPaneRow via Vite SSR + jsdom): idle, ratio ~4.1-5.3x (calibMs
 * ~6.3-10.1ms, renderMs ~33.7-41.1ms across 5 runs). The failing
 * Actions-runner sample (348.2ms, task-5419's spec) is >3x the old 100ms
 * wall-clock budget purely from host contention -- a calib probe on that
 * same contended host inflates proportionally (same "host load cancels out
 * of the ratio" property idempotencyScanRatchet.mjs documents), so 12x
 * leaves >2x headroom over the worst observed idle ratio here, matching the
 * same multiplier the api-client precedent settled on instead of
 * re-deriving a new fudge factor per bench.
 */
export const RATIO_MULTIPLIER = 12;

const CALIB_SAMPLES = 5;
/**
 * Sized so calibMs lands in the same single-digit-to-low-double-digit ms
 * order of magnitude as the isolated renderMs this gates (a React 19
 * client-root render of a 1000-node SVG tree), for the same reason
 * idempotencyScanRatchet.mjs's CALIB_INNER_ITERATIONS docstring gives: a
 * probe orders of magnitude faster than the thing it normalizes is
 * dominated by single-GC-pause jitter, which the division in
 * decideBenchOutcome then amplifies into a noisier ratio.
 */
const CALIB_INNER_ITERATIONS = 400;
const CALIB_BUFFER_SIZE = 2048;

/**
 * Fixed-iteration float/typed-array workload with no dependency on React,
 * jsdom, or ChartPaneRow -- its only job is to answer "how loaded is this
 * host right now", independent of anything under test. `acc` is folded into
 * a never-thrown check purely so the JIT can't prove the loop body is dead
 * and elide it.
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
 * Pure decision function (no fs/console/DOM side effects) so it can be
 * tested against fabricated renderMs/calibMs pairs instead of a real render.
 */
export function decideBenchOutcome(renderMs, calibMs, ratioMultiplier = RATIO_MULTIPLIER) {
  const ratio = calibMs > 0 ? renderMs / calibMs : Infinity;
  const budgetMs = calibMs * ratioMultiplier;
  const ok = renderMs <= budgetMs;
  const message = ok
    ? `[chart-pane-row-bench] OK: renderMs=${renderMs.toFixed(3)} calibMs=${calibMs.toFixed(3)} ratio=${ratio.toFixed(2)}x (budget ${ratioMultiplier}x, ${budgetMs.toFixed(3)}ms)`
    : `[chart-pane-row-bench] FAIL: renderMs=${renderMs.toFixed(3)} exceeds ${ratioMultiplier}x calibMs=${calibMs.toFixed(3)} (budget ${budgetMs.toFixed(3)}ms, actual ratio ${ratio.toFixed(2)}x)`;
  return { ok, ratio, budgetMs, exitCode: ok ? 0 : 1, message };
}
