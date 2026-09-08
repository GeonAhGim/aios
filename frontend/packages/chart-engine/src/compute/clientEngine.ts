/**
 * CH-18a — client-side incremental indicator engine: a bar-by-bar TS port of
 * the backend IND-1 streaming kernel (`src/core/indicators/engine/incremental.py`,
 * task-1738 IND-7g reference vectors), restricted to the whitelist gate in
 * `verifiedIndicators.ts`.
 *
 * Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11
 * CH-18 (compute path + whitelist gate only — actual server/client equivalence
 * checking and fallback wiring is CH-18b, task-1954).
 *
 * Only the 10 names IND-7g actually verified are ported (BBANDS is a
 * `VERIFIABLE_NAMES` candidate but fails 3-way verification at a param
 * boundary — see `verifiedIndicators.ts` module docstring — so it has no
 * kernel here at all, not merely an unpinned one).
 *
 * Decision (task-1953): no new OSS dependency (no `technicalindicators`,
 * no TA-Lib WASM) — every kernel here is written by hand from the Python
 * state machine so the numeric recipe (window re-sum every bar, SMA-seeded
 * EMA, Wilder smoothing, TA-Lib's exact 0-division rules) matches bit for
 * bit. klinecharts' bundled indicator math is never used as a substitute —
 * it isn't verified against the server (IND-7g) and would silently defeat
 * CH-18's purpose (server/client parity). `createClientIncrementalIndicator`
 * is the only entry point and it re-checks the whitelist itself — a caller
 * cannot bypass the gate by holding onto a kernel name.
 *
 * task-2098 (P6 300-line cap): the bar/param types, `ClientEngineError`, and
 * the `Window`/`Ema`/`Wilder` numeric primitives moved to `kernelPrimitives.ts`;
 * the 10 `create*` kernel factories moved to `kernelFactories.ts`. Both are
 * re-exported here so existing imports of `./clientEngine` keep working.
 */

import type { IndicatorCatalogEntry } from "../plugins/indicatorPlugin";
import { KERNEL_FACTORIES } from "./kernelFactories";
import { ClientEngineError, assertBarInputs } from "./kernelPrimitives";
import type { Bar, IncrementalIndicator, IndicatorOutputs, IndicatorParams } from "./kernelPrimitives";
import { resolveVerifiedIndicators } from "./verifiedIndicators";

export type { Bar, ClientEngineErrorCode, IncrementalIndicator, IndicatorOutputs, IndicatorParams } from "./kernelPrimitives";
export { ClientEngineError } from "./kernelPrimitives";
export { KERNEL_FACTORIES } from "./kernelFactories";

/**
 * The only entry point into the client engine. Re-derives the whitelist from
 * `catalog` itself (never trusts a caller's say-so) and refuses to build a
 * kernel for anything outside it.
 */
export function createClientIncrementalIndicator(
  name: string,
  params: IndicatorParams,
  catalog: readonly IndicatorCatalogEntry[] | null | undefined,
): IncrementalIndicator {
  const verified = resolveVerifiedIndicators(catalog);
  const entry = verified.get(name);
  if (!entry) {
    throw new ClientEngineError(
      "CLIENT_ENGINE_INDICATOR_NOT_VERIFIED",
      name,
      "not in the server catalog's verified whitelist (missing, tier mismatch, or entry_hash drift) — client-side compute refused",
    );
  }
  const factory = KERNEL_FACTORIES[name];
  if (!factory) {
    throw new ClientEngineError("CLIENT_ENGINE_INDICATOR_UNKNOWN", name, "no ported client kernel for this indicator");
  }
  const state = factory(params, name);
  const outputs = entry.outputs;
  const inputs = entry.inputs;
  return {
    name,
    update(bar: Bar): IndicatorOutputs {
      assertBarInputs(name, inputs, bar);
      const values = state.update(bar);
      if (values === null) {
        return Object.fromEntries(outputs.map((output) => [output, null]));
      }
      return Object.fromEntries(outputs.map((output, index) => [output, values[index]!]));
    },
  };
}

export interface ComputeIndicatorSeriesArgs {
  readonly name: string;
  readonly params: IndicatorParams;
  readonly bars: readonly Bar[];
  readonly catalog: readonly IndicatorCatalogEntry[] | null | undefined;
}

export type IndicatorSeriesResult = Readonly<Record<string, ReadonlyArray<number | null>>>;

/** Batch helper over `createClientIncrementalIndicator` — the task body a `workerPool.ts` backend runs. */
export function computeIndicatorSeries(args: ComputeIndicatorSeriesArgs): IndicatorSeriesResult {
  const indicator = createClientIncrementalIndicator(args.name, args.params, args.catalog);
  const series: Record<string, (number | null)[]> = {};
  for (const bar of args.bars) {
    const values = indicator.update(bar);
    for (const [key, value] of Object.entries(values)) {
      (series[key] ??= []).push(value);
    }
  }
  return series;
}

/** Task name a `workerPool.ts` backend registers `computeIndicatorSeries` under. */
export const CLIENT_ENGINE_COMPUTE_TASK = "chart-engine/compute-indicator-series";
