/** Fastest-of-N measurement helpers for performance assertions.

 * Copied from apps/web/src/test/perfBudget.ts so api-client tests can
 * best-of measure without crossing package boundaries. */

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
