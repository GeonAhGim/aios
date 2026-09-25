import type { IndicatorStyleOutput } from "../../plugins/indicatorPlugin";
import type { HistogramBar, PlotRenderTarget, PlotSpec } from "../plotRenderers";
import type { PlotProjection } from "../scaleBinding";

export const IDENTITY_PROJECTION: PlotProjection = { timeToX: (t) => t, priceToY: (v) => v };

export const VISIBLE_STYLE: IndicatorStyleOutput = { output: "value", color: "#fff", lineWidth: 1, visible: true };
export const HIDDEN_STYLE: IndicatorStyleOutput = { ...VISIBLE_STYLE, visible: false };

export function spec(overrides: Partial<PlotSpec> = {}): PlotSpec {
  return {
    kind: "line",
    scale: "own",
    default_pane: "separate",
    fill_between: null,
    color_rule: null,
    precision: null,
    legend_format: null,
    ...overrides,
  };
}

export function recordingTarget(): PlotRenderTarget & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    drawLine: () => calls.push("drawLine"),
    drawHistogram: () => calls.push("drawHistogram"),
    drawArea: () => calls.push("drawArea"),
    drawPolygon: () => calls.push("drawPolygon"),
    drawMarker: () => calls.push("drawMarker"),
  };
}

export const POINTS = [
  { time: 1, value: 10 },
  { time: 2, value: 12 },
];

/** Unlike `recordingTarget`, captures the actual `style` object per call so a test can compare
 * arguments structurally (color A != color B) instead of asserting a hardcoded color literal. */
export function spyTarget(): PlotRenderTarget & {
  histogramCalls: { bars: readonly HistogramBar[]; style: IndicatorStyleOutput }[];
  polygonCalls: { style: IndicatorStyleOutput }[];
} {
  const histogramCalls: { bars: readonly HistogramBar[]; style: IndicatorStyleOutput }[] = [];
  const polygonCalls: { style: IndicatorStyleOutput }[] = [];
  return {
    histogramCalls,
    polygonCalls,
    drawLine: () => {},
    drawHistogram: (bars, style) => histogramCalls.push({ bars, style }),
    drawArea: () => {},
    drawPolygon: (_points, style) => polygonCalls.push({ style }),
    drawMarker: () => {},
  };
}
