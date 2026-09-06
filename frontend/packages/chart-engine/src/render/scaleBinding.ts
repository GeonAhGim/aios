/**
 * CH-15 — binds a `PlotSpec.scale` hint to a pixel `PlotProjection`. Reuses
 * CH-1b `core/priceScale.ts`/`core/timeScale.ts` and CH-14
 * `panes/paneLayout.ts` `PaneScaleSet` as-is (decision, task-1732: no new
 * scale concept) — this module only chooses *which* existing `PriceScale`
 * answers `priceToY` and applies a coordinate transform on top of it for the
 * two hints (`log`, `inverted`) that need one:
 *
 *  - "own"/"percent": the indicator's own pane scale, untouched. (Both hints
 *    use a dedicated pane scale; "percent" differs only in axis label
 *    formatting, which is CH-16 legend territory, not this leaf.)
 *  - "overlay": the main (candle) pane's scale, so the plot shares its
 *    coordinate space with price.
 *  - "log": the own-pane scale, with domain validation (`priceToY` throws on
 *    <= 0 — log of a non-positive price is undefined) before delegating.
 *    Real logarithmic curvature comes from a `PriceScaleBackend` when one is
 *    mounted (CH-1b's documented backend-delegates-first, linear-fallback
 *    contract) — this module does not re-derive log math itself.
 *  - "inverted": the own-pane scale's `priceToY`, mirrored across the pane's
 *    height (`height - y`) so higher values draw lower.
 */

import type { PriceScale } from "../core/priceScale";
import type { TimeScale } from "../core/timeScale";
import type { ScaleHint } from "./plotRenderers";

export type ScaleBindingErrorCode = "SCALE_BINDING_UNKNOWN_SCALE" | "SCALE_BINDING_NON_POSITIVE_FOR_LOG";

export class ScaleBindingError extends Error {
  readonly code: ScaleBindingErrorCode;

  constructor(code: ScaleBindingErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ScaleBindingError";
    this.code = code;
  }
}

const SCALE_HINTS: ReadonlySet<string> = new Set<ScaleHint>(["own", "overlay", "percent", "log", "inverted"]);

export interface ScaleBindingContext {
  /** The main (candle) pane's scale — used for "overlay". */
  readonly mainScale: PriceScale;
  /** This indicator's own pane scale — used for "own"/"percent"/"log"/"inverted". */
  readonly ownScale: PriceScale;
  readonly timeScale: TimeScale;
}

export interface PlotProjection {
  timeToX(time: number): number;
  priceToY(value: number): number;
}

/**
 * Runtime-validates `scale` even though callers normally already went through
 * `decodePlotSpec` — this module must reject a bad value on its own so it
 * stays independently testable and safe against data that bypassed decode.
 */
export function bindScale(scale: ScaleHint, context: ScaleBindingContext): PlotProjection {
  if (!SCALE_HINTS.has(scale)) {
    throw new ScaleBindingError("SCALE_BINDING_UNKNOWN_SCALE", `unknown scale: ${JSON.stringify(scale)}`);
  }
  const timeToX = (time: number): number => context.timeScale.timeToX(time);

  switch (scale) {
    case "overlay":
      return { timeToX, priceToY: (value) => context.mainScale.priceToY(value) };
    case "own":
    case "percent":
      return { timeToX, priceToY: (value) => context.ownScale.priceToY(value) };
    case "log":
      return {
        timeToX,
        priceToY: (value) => {
          if (!(value > 0)) {
            throw new ScaleBindingError("SCALE_BINDING_NON_POSITIVE_FOR_LOG", `log scale requires value > 0, got ${value}`);
          }
          return context.ownScale.priceToY(value);
        },
      };
    case "inverted":
      return { timeToX, priceToY: (value) => context.ownScale.height - context.ownScale.priceToY(value) };
    default: {
      const exhaustive: never = scale;
      throw new ScaleBindingError("SCALE_BINDING_UNKNOWN_SCALE", `unhandled scale: ${String(exhaustive)}`);
    }
  }
}
