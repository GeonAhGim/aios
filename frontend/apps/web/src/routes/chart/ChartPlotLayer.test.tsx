import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { createPriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import { buildPlotLayer, priceRangeFromCandles, priceRangeFromSeries, timeRangeFromCandles, type OverlaySeriesByOutput } from "./ChartPlotLayer";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";

function candle(hourOffset: number, close: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: open.toISOString(),
      open: String(close),
      high: String(close + 10),
      low: String(close - 10),
      close: String(close),
      volume: "1",
      quote_volume: "1",
    },
  };
}

const CANDLES = [candle(0, 100), candle(1, 110), candle(2, 105)];
const TIME_SCALE = createTimeScale({ range: timeRangeFromCandles(CANDLES), width: 600 });
const MAIN_SCALE = createPriceScale({ range: priceRangeFromCandles(CANDLES), height: 300 });

function overlay(id: string, placement: OverlayEntry["placement"], outputs: OverlayEntry["outputs"]): OverlayEntry {
  return { id, placement, params: [], outputs, paneIndex: placement === "main-overlay" ? 0 : 1 };
}

function series(points: readonly { time: number; value: number }[]): readonly { time: number; value: number }[] {
  return points;
}

describe("buildPlotLayer", () => {
  it("draws a line for a plain sub-pane line output", () => {
    const rsi = overlay("RSI", "sub-pane", [{ name: "value", series: "line" }]);
    const seriesByOutput: OverlaySeriesByOutput = new Map([["value", series(CANDLES.map((c) => ({ time: c.openTimeMs, value: 50 })))]]);
    const ownScale = createPriceScale({ range: priceRangeFromSeries(seriesByOutput), height: 100 });
    const result = buildPlotLayer({
      overlays: [rsi],
      overlaySeries: new Map([["RSI", seriesByOutput]]),
      overlayPlotSpecs: new Map(),
      mainScale: MAIN_SCALE,
      ownScale,
      timeScale: TIME_SCALE,
    });
    expect(result.issues).toEqual([]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
  });

  it("DoD: two brand-new PlotSpec kinds (line + band/fill_between) both render with zero screen-code changes", () => {
    const newLineIndicator = overlay("BRAND_NEW_LINE", "sub-pane", [{ name: "value", series: "line" }]);
    const newBandIndicator = overlay("BRAND_NEW_BAND", "main-overlay", [
      { name: "upperband", series: "line" },
      { name: "lowerband", series: "line" },
    ]);
    const points = CANDLES.map((c, i) => ({ time: c.openTimeMs, value: 100 + i }));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([
      ["BRAND_NEW_LINE", new Map([["value", series(points)]])],
      [
        "BRAND_NEW_BAND",
        new Map([
          ["upperband", series(points.map((p) => ({ ...p, value: p.value + 5 })))],
          ["lowerband", series(points.map((p) => ({ ...p, value: p.value - 5 })))],
        ]),
      ],
    ]);
    const result = buildPlotLayer({
      overlays: [newLineIndicator, newBandIndicator],
      overlaySeries,
      overlayPlotSpecs: new Map(),
      mainScale: MAIN_SCALE,
      ownScale: MAIN_SCALE,
      timeScale: TIME_SCALE,
    });
    expect(result.issues).toEqual([]);
    const { container } = render(<svg>{result.nodes}</svg>);
    // line output -> one polyline; band output -> one polyline (the line) + one polygon (the fill).
    expect(container.querySelectorAll("polyline").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll("polygon")).toHaveLength(1);
  });

  it("negative: an unknown PlotSpec.kind is rejected explicitly, not silently skipped", () => {
    const overlays = [overlay("WEIRD", "sub-pane", [{ name: "value", series: "line" }])];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["WEIRD", new Map([["value", series([{ time: 1, value: 1 }])]])]]);
    const overlayPlotSpecs = new Map([
      [
        "WEIRD",
        new Map<string, unknown>([
          ["value", { kind: "__unknown__", scale: "own", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null }],
        ]),
      ],
    ]);
    const result = buildPlotLayer({
      overlays,
      overlaySeries,
      overlayPlotSpecs,
      mainScale: MAIN_SCALE,
      ownScale: MAIN_SCALE,
      timeScale: TIME_SCALE,
    });
    expect(result.nodes).toEqual([]);
    expect(result.issues).toEqual([{ overlayId: "WEIRD", output: "value", code: "PLOT_RENDER_UNKNOWN_KIND" }]);
  });

  it("an overlay with no series data yet draws nothing and raises no issue (not-yet-computed is not a rejection)", () => {
    const overlays = [overlay("NOT_COMPUTED_YET", "sub-pane", [{ name: "value", series: "line" }])];
    const result = buildPlotLayer({
      overlays,
      overlaySeries: new Map(),
      overlayPlotSpecs: new Map(),
      mainScale: MAIN_SCALE,
      ownScale: MAIN_SCALE,
      timeScale: TIME_SCALE,
    });
    expect(result.nodes).toEqual([]);
    expect(result.issues).toEqual([]);
  });
});
