/**
 * CH-16 — statusLine: the vendor-free half of the OHLCV "status line" split
 * (task-2045, reusing task-2044's dataWindow.ts/dataWindowStyle.ts type
 * boundary — see that file's docstring). `buildStatusLineLegends` is the pure
 * value-selection-and-formatting logic meant to be handed to the vendor's
 * `candle.tooltip.legend.template` callback (§9.11 CH-16 table); the style
 * binding that actually references vendor `CandleTooltipStyle`/`CrosshairStyle`
 * types lives in `statusLineStyle.ts` instead, because typing anything out of
 * `../core/klinecharts` — even type-only — drags the whole `vendor/klinecharts`
 * source tree into any tsc program that reaches it, and that vendor source
 * isn't authored against apps/web's verbatimModuleSyntax/erasableSyntaxOnly
 * flags. Keeping this module vendor-free is what lets apps/web import it.
 *
 * Fail-closed by construction, not by exception: a missing candle (no data
 * yet, or a crosshair resolved past the edge of the series) has no numbers
 * to show, so every field renders `options.defaultValue` instead of the
 * function throwing — a status line that can vanish mid-drag is worse than
 * one showing placeholders.
 */

/** Structurally identical to vendor `KLineData` (`../core/klinecharts`), redeclared to stay vendor-free. */
export interface StatusLineCandle {
  readonly timestamp: number;
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
  readonly volume?: number;
}

/** Structurally identical to vendor `NeighborData<T>` (`../core/klinecharts`), redeclared to stay vendor-free. */
export interface StatusLineNeighbor<T> {
  readonly prev: T;
  readonly current: T;
  readonly next: T;
}

/** Structurally identical to vendor `TooltipLegend` (`../core/klinecharts`), redeclared to stay vendor-free. */
export interface StatusLineLegend {
  readonly title: string;
  readonly value: string | { readonly text: string; readonly color: string };
}

export interface StatusLineOptions {
  readonly pricePrecision?: number;
  readonly volumePrecision?: number;
  readonly upColor?: string;
  readonly downColor?: string;
  readonly noChangeColor?: string;
  readonly defaultValue?: string;
}

const DEFAULTS: Required<StatusLineOptions> = {
  pricePrecision: 2,
  volumePrecision: 0,
  upColor: "#2DC08E",
  downColor: "#F92855",
  noChangeColor: "#76808F",
  defaultValue: "--",
};

function resolveOptions(options: StatusLineOptions): Required<StatusLineOptions> {
  return { ...DEFAULTS, ...options };
}

function formatFixed(value: number | undefined, precision: number, defaultValue: string): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(precision) : defaultValue;
}

function formatSigned(value: number | undefined, precision: number, defaultValue: string, suffix = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return defaultValue;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(precision)}${suffix}`;
}

function changeColor(change: number | undefined, resolved: Required<StatusLineOptions>): string {
  if (typeof change !== "number" || !Number.isFinite(change) || change === 0) return resolved.noChangeColor;
  return change > 0 ? resolved.upColor : resolved.downColor;
}

/**
 * Pure OHLCV row builder — the actual "crosshair value display" logic.
 * `data.current === null` covers both "no candles loaded yet" and "crosshair
 * resolved to an index outside the series", matching the vendor's own
 * `NeighborData` contract this is designed to bind onto (`statusLineStyle.ts`).
 */
export function buildStatusLineLegends(
  data: StatusLineNeighbor<StatusLineCandle | null>,
  options: StatusLineOptions = {},
): StatusLineLegend[] {
  const resolved = resolveOptions(options);
  const candle = data.current;
  if (candle === null) {
    return ["O", "H", "L", "C", "Vol", "Chg", "Chg%"].map((title) => ({ title, value: resolved.defaultValue }));
  }

  const prevClose = data.prev?.close;
  const changeAbs = typeof prevClose === "number" ? candle.close - prevClose : undefined;
  const changePct = typeof prevClose === "number" && prevClose !== 0 ? (changeAbs! / prevClose) * 100 : undefined;
  const color = changeColor(changeAbs, resolved);

  return [
    { title: "O", value: formatFixed(candle.open, resolved.pricePrecision, resolved.defaultValue) },
    { title: "H", value: formatFixed(candle.high, resolved.pricePrecision, resolved.defaultValue) },
    { title: "L", value: formatFixed(candle.low, resolved.pricePrecision, resolved.defaultValue) },
    { title: "C", value: { text: formatFixed(candle.close, resolved.pricePrecision, resolved.defaultValue), color } },
    { title: "Vol", value: formatFixed(candle.volume, resolved.volumePrecision, resolved.defaultValue) },
    { title: "Chg", value: { text: formatSigned(changeAbs, resolved.pricePrecision, resolved.defaultValue), color } },
    { title: "Chg%", value: { text: formatSigned(changePct, 2, resolved.defaultValue, "%"), color } },
  ];
}
