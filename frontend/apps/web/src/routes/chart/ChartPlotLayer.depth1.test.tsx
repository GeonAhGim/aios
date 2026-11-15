// CH-15c depth D1->D2: negative x3+, failure injection, perf assertion, gate red.
import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { createPriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import {
  PLOT_ERROR_REASONS,
  buildPlotLayer,
  priceRangeFromCandles,
  timeRangeFromCandles,
  type OverlaySeriesByOutput,
  type PlotLayerInput,
} from "./ChartPlotLayer";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { ScaleBindingError, bindScale } from "@aios/chart-engine/src/render/scaleBinding";
import { perfBudgetMs } from "../../test/perfBudget";

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

const RSI = overlay("RSI", "sub-pane", [{ name: "value", series: "line" }]);
const BAND = overlay("BAND", "main-overlay", [
  { name: "upperband", series: "line" },
  { name: "lowerband", series: "line" },
]);

function inputFor(
  series: ReadonlyMap<string, OverlaySeriesByOutput>,
  specs: ReadonlyMap<string, ReadonlyMap<string, unknown>> = new Map(),
  overlays: readonly OverlayEntry[] = [RSI],
): PlotLayerInput {
  return {
    overlays,
    overlaySeries: series,
    overlayPlotSpecs: specs,
    mainScale: MAIN_SCALE,
    ownScale: MAIN_SCALE,
    timeScale: TIME_SCALE,
  };
}

const rsiPoints = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ time: CANDLES[0]!.openTimeMs + i * 3_600_000, value: 50 + Math.sin(i / 50) * 20 }));

function rsiInput(n: number, spec?: unknown): PlotLayerInput {
  const specs = spec === undefined ? new Map() : new Map([["RSI", new Map([["value", spec]])]]);
  return inputFor(new Map([["RSI", new Map([["value", rsiPoints(n)]])]]), specs);
}

/** BAND whose upper/lower partner series differ in length -> FILL_BETWEEN_LENGTH_MISMATCH. */
function mismatchedBandInput(lowerLen: number): PlotLayerInput {
  const upper = rsiPoints(1);
  const lower = rsiPoints(lowerLen);
  const bandSpec = {
    kind: "band",
    scale: "overlay",
    default_pane: "price",
    fill_between: "lowerband",
    color_rule: null,
    precision: null,
    legend_format: null,
  };
  return inputFor(
    new Map([
      ["BAND", new Map([["upperband", upper], ["lowerband", lower]])],
      ["RSI", new Map([["value", rsiPoints(3)]])],
    ]),
    new Map([["BAND", new Map([["upperband", bandSpec]])]]),
    [BAND, RSI],
  );
}

describe("buildPlotLayer D2 depth", () => {
  it("negative: unknown scale throws ScaleBindingError from bindScale (toThrow)", () => {
    const ctx = { mainScale: MAIN_SCALE, ownScale: MAIN_SCALE, timeScale: TIME_SCALE };
    expect(() => bindScale("phantom_scale" as never, ctx)).toThrow(ScaleBindingError);
  });

  it("negative: unknown plot kind is recorded as an issue and draws no polyline", () => {
    const result = buildPlotLayer(rsiInput(3, { kind: "nonexistent_kind_xyz" }));
    expect(result.issues).toEqual([{ overlayId: "RSI", output: "value", code: "PLOT_RENDER_UNKNOWN_KIND" }]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelector("polyline")).not.toBeInTheDocument();
  });

  it("negative: empty series draws nothing and surfaces no role=alert", () => {
    const result = buildPlotLayer(inputFor(new Map([["RSI", new Map()]])));
    expect(result.issues).toEqual([]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelector("polyline")).not.toBeInTheDocument();
    expect(container.querySelector('[role="alert"]')).not.toBeInTheDocument();
  });

  it("negative: every issue code maps to a reason string, never a raw message", () => {
    const result = buildPlotLayer(rsiInput(3, { kind: "line", scale: "phantom_scale" }));
    expect(result.issues).toHaveLength(1);
    const reason = PLOT_ERROR_REASONS[result.issues[0]!.code];
    expect(reason).toBeTruthy();
    expect(reason).not.toMatch(/Error|undefined/);
  });

  it("failure injection: mismatched fill_between partner is isolated, healthy overlay still renders", () => {
    const result = buildPlotLayer(mismatchedBandInput(2));
    expect(result.issues).toEqual([{ overlayId: "BAND", output: "upperband", code: "FILL_BETWEEN_LENGTH_MISMATCH" }]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelectorAll("polyline").length).toBeGreaterThanOrEqual(1);
  });

  // Measured locally: ~2ms for 1000 points (plain run). Budget 60ms = 30x headroom,
  // still catches an O(n^2) regression; perfBudgetMs widens it under coverage.
  it("perf: buildPlotLayer on 1000 points stays under budget", () => {
    const input = rsiInput(1000);
    const start = performance.now();
    const result = buildPlotLayer(input);
    const elapsed = performance.now() - start;
    expect(result.issues).toEqual([]);
    expect(elapsed).toBeLessThan(perfBudgetMs(60));
  });

  it("gate red: same assertion fails when the input is broken (matching partner lengths clear the issue)", () => {
    const broken = buildPlotLayer(mismatchedBandInput(2));
    const healthy = buildPlotLayer(mismatchedBandInput(1));
    expect(broken.issues).toHaveLength(1);
    expect(healthy.issues).toEqual([]);
    // The "issues empty" gate passes only for the healthy input; the broken one must fail it.
    expect(() => expect(broken.issues).toEqual([])).toThrow();
  });

  it("gate red: a blown latency budget makes the perf assertion fail", () => {
    const elapsed = 10_000; // injected delay
    expect(() => expect(elapsed).toBeLessThan(perfBudgetMs(60))).toThrow();
  });
});
