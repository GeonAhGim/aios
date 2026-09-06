/**
 * CH-16 — statusLine: style binding onto vendor `CandleTooltipView` +
 * `CrosshairFeatureView` (§9.11 CH-16 table). Per the spec, this file has no
 * tooltip drawing of its own — the vendor canvas view still draws every
 * pixel. What this module owns is the OHLCV row's *value selection and
 * formatting* (`buildStatusLineLegends`), handed to the vendor as the
 * `candle.tooltip.legend.template` callback, plus the small style overrides
 * (`showRule: "always"`) that make the row a persistent "status line" driven
 * by the crosshair's resolved bar rather than only appearing on hover.
 *
 * Fail-closed by construction, not by exception: a missing candle (no data
 * yet, or a crosshair resolved past the edge of the series) has no numbers
 * to show, so every field renders `options.defaultValue` instead of the
 * function throwing — a status line that can vanish mid-drag is worse than
 * one showing placeholders.
 */

import type { CandleStyle, CandleTooltipStyle, CrosshairStyle, DeepPartial, KLineData, NeighborData, TooltipLegend } from "../core/klinecharts";

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
 * resolved to an index outside the series", since the vendor passes `null`
 * for both (CH-14 `crosshairSync.ts` only tracks `timeMs`; resolving that to
 * a candle, and clamping out-of-range indexes to `null`, is the vendor's
 * `NeighborData` contract this binds to).
 */
export function buildStatusLineLegends(
  data: NeighborData<KLineData | null>,
  options: StatusLineOptions = {},
): TooltipLegend[] {
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

/** Binds `buildStatusLineLegends` into the vendor style shape for `chart.setStyles({ candle: { tooltip: ... } })`. */
export function createStatusLineTooltipStyle(options: StatusLineOptions = {}): DeepPartial<CandleTooltipStyle> {
  return {
    showRule: "always",
    legend: {
      defaultValue: resolveOptions(options).defaultValue,
      template: (data: NeighborData<KLineData | null>, _styles: CandleStyle) => buildStatusLineLegends(data, options),
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
