// Coverage-aware wall-clock budgets for the "performance assertion" tests.
//
// Those tests render a deliberately large fixture and assert an upper bound on
// elapsed time so an O(n^2) regression is caught. The bounds were tuned for a
// plain `vitest run`. Under `vitest run --coverage` (what CI's `test:coverage`
// runs) V8 precise coverage instruments every executed function in the worker,
// and render-heavy React code -- thousands of tiny hot functions -- slows down
// by a constant factor without any algorithmic change. Combined with the CPU
// contention the shared CI host already suffers (task-1968), four unrelated
// suites went red at once by 1.2x-2.1x (task-3460): the same "unrelated files,
// same signal" pattern task-1968 documented for timeouts.
//
// `perfBudgetMs(base)` therefore widens `base` by a fixed factor only when the
// run is a coverage run. The factor is small enough that a genuine quadratic
// blow-up -- 10x or more on these fixtures -- still fails, and a plain run keeps
// the original budget untouched. Coverage mode is signalled by
// `VITEST_COVERAGE=1`, which vitest.config.ts sets from the CLI flags.

/** Slowdown allowed for V8 precise coverage instrumentation. */
export const COVERAGE_SCALE = 3;

/** True when this worker runs under `vitest run --coverage`. */
export function coverageEnabled(): boolean {
  return import.meta.env.VITEST_COVERAGE === "1";
}

/** Multiplier applied to plain-run budgets: 1 for plain runs, `COVERAGE_SCALE` under coverage. */
export function perfScale(): number {
  return coverageEnabled() ? COVERAGE_SCALE : 1;
}

/** `baseMs` (the plain-run budget) widened for the current run mode. */
export function perfBudgetMs(baseMs: number): number {
  return baseMs * perfScale();
}

// A single wall-clock sample of a cold render measures the shared host as much
// as the code: the first render in a worker pays JIT, module and CSS warm-up,
// and under `--coverage` on a loaded runner one sample read 87ms against a 60ms
// budget for a component that renders in ~2ms (Actions quality.yml, 2026-11-15).
// A genuine slowdown (O(n^2), a sync wait) is slow in EVERY run, so the budget
// is judged on the fastest of a few runs; the budget numbers themselves stay.

/** Runs per measurement; enough to dodge a one-off stall, few enough to stay cheap. */
export const BEST_OF_RUNS = 5;

/** Fastest of `runs` calls of `measure`, each returning its own elapsed ms. */
export function bestOfMs(measure: () => number, runs: number = BEST_OF_RUNS): number {
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < runs; i += 1) best = Math.min(best, measure());
  return best;
}

/** Async twin of `bestOfMs` (the runs are sequential, never concurrent). */
export async function bestOfMsAsync(
  measure: () => Promise<number>,
  runs: number = BEST_OF_RUNS,
): Promise<number> {
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < runs; i += 1) best = Math.min(best, await measure());
  return best;
}
