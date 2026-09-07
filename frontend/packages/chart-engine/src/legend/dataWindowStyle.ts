/**
 * CH-16 — dataWindow style binding onto vendor `IndicatorTooltipView` /
 * `IndicatorLastValueView` (§9.11 CH-16 table). This is the vendor-typed
 * half of dataWindow split out of `dataWindow.ts` (task-2044): any file that
 * types anything out of `../core/klinecharts` drags the whole
 * `vendor/klinecharts` source tree into whatever TS program reaches it,
 * because `core/klinecharts.ts` re-exports types that themselves resolve
 * through the vendor's own barrel (`vendor/klinecharts/src/index`), and that
 * barrel is not authored against apps/web's
 * verbatimModuleSyntax/erasableSyntaxOnly tsconfig flags. Nothing under
 * apps/web may import this file — only `dataWindow.ts`'s vendor-free exports
 * are safe there (see that file's docstring and
 * `apps/web/.../ChartPanes.tsx`'s header comment).
 */

import type { DeepPartial, IndicatorLastValueMarkStyle, IndicatorTooltipStyle } from "../core/klinecharts";
import type { DataWindowOptions } from "./dataWindow";

const DEFAULT_VALUE = "n/a";

/** Binds the "always show, never suppress on 30 indicators" rule into `chart.setStyles({ indicator: { tooltip: ... } })`. */
export function createIndicatorTooltipStyle(options: DataWindowOptions = {}): DeepPartial<IndicatorTooltipStyle> {
  return {
    showRule: "always",
    legend: { defaultValue: options.defaultValue ?? DEFAULT_VALUE },
  };
}

/** Binds the Y-axis last-value marks (`IndicatorLastValueView`) that accompany the data window. */
export function createIndicatorLastValueMarkStyle(show = true): DeepPartial<IndicatorLastValueMarkStyle> {
  return { show };
}
