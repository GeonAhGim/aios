/**
 * CH-16 — statusLine style binding onto vendor `CandleTooltipView` /
 * `CrosshairFeatureView` (§9.11 CH-16 table). This is the vendor-typed half
 * of statusLine split out of `statusLine.ts` (task-2045, same split task-2044
 * used for `dataWindow.ts`/`dataWindowStyle.ts`): any file that types
 * anything out of `../core/klinecharts` drags the whole `vendor/klinecharts`
 * source tree into whatever tsc program reaches it, because that vendor
 * source isn't authored against apps/web's
 * verbatimModuleSyntax/erasableSyntaxOnly tsconfig flags. Nothing under
 * apps/web may import this file — only `statusLine.ts`'s vendor-free exports
 * are safe there (see that file's docstring and
 * `apps/web/.../ChartPanes.tsx`'s header comment).
 */

import type { CandleStyle, CandleTooltipStyle, CrosshairStyle, DeepPartial, KLineData, NeighborData, TooltipLegend } from "../core/klinecharts";
import { buildStatusLineLegends, type StatusLineOptions } from "./statusLine";

/** Binds `buildStatusLineLegends` into the vendor style shape for `chart.setStyles({ candle: { tooltip: ... } })`. */
export function createStatusLineTooltipStyle(options: StatusLineOptions = {}): DeepPartial<CandleTooltipStyle> {
  return {
    showRule: "always",
    legend: {
      defaultValue: (options.defaultValue ?? "--"),
      template: (data: NeighborData<KLineData | null>, _styles: CandleStyle): TooltipLegend[] => buildStatusLineLegends(data, options),
    },
  };
}

export interface CrosshairValueOptions {
  readonly showAxisLabel?: boolean;
}

/**
 * `CrosshairFeatureView` and the axis price/time labels it sits alongside
 * both read `styles.crosshair` — this is the "값 표시" (value display) half
 * of that shared config; icon buttons (features) are left at vendor
 * defaults since CH-16's DoD is about values, not click actions.
 */
export function createCrosshairValueStyle(options: CrosshairValueOptions = {}): DeepPartial<CrosshairStyle> {
  const show = options.showAxisLabel ?? true;
  return {
    horizontal: { text: { show } },
    vertical: { text: { show } },
  };
}
