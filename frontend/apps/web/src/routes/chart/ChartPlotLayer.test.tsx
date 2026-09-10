import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { createPriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import {
  buildPlotLayer,
  priceRangeFromCandles,
  priceRangeFromSeries,
  timeRangeFromCandles,
  type OverlaySeriesByOutput,
  type PlotLayerInput,
} from "./ChartPlotLayer";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import {
  PlotRenderError,
  decodePlotSpec,
  deriveOverlayPlotSpec,
  renderPlot,
  type PlotRenderTarget,
} from "@aios/chart-engine/src/render/plotRenderers";
import { ScaleBindingError, bindScale } from "@aios/chart-engine/src/render/scaleBinding";
import { FillBetweenError } from "@aios/chart-engine/src/render/fillBetween";

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

  it("negative: fill_between partner series with a mismatched length is rejected, not silently skipped", () => {
    const overlays = [
      overlay("MISMATCHED_BAND", "main-overlay", [
        { name: "upperband", series: "line" },
        { name: "lowerband", series: "line" },
      ]),
    ];
    const points = CANDLES.map((c, i) => ({ time: c.openTimeMs, value: 100 + i }));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([
      [
        "MISMATCHED_BAND",
        new Map([
          ["upperband", series(points)],
          ["lowerband", series(points.slice(0, points.length - 1))],
        ]),
      ],
    ]);
    const overlayPlotSpecs = new Map([
      [
        "MISMATCHED_BAND",
        new Map<string, unknown>([
          [
            "upperband",
            { kind: "band", scale: "overlay", default_pane: "price", fill_between: "lowerband", color_rule: null, precision: null, legend_format: null },
          ],
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
    expect(result.issues).toEqual([{ overlayId: "MISMATCHED_BAND", output: "upperband", code: "FILL_BETWEEN_LENGTH_MISMATCH" }]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelectorAll("polygon")).toHaveLength(0);
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

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Corruption =
  | "unknown_kind"
  | "unknown_scale"
  | "unknown_field"
  | "fill_target_missing"
  | "fill_length_mismatch"
  | "fill_time_mismatch"
  | "log_non_positive";

const CORRUPTIONS: readonly Corruption[] = [
  "unknown_kind",
  "unknown_scale",
  "unknown_field",
  "fill_target_missing",
  "fill_length_mismatch",
  "fill_time_mismatch",
  "log_non_positive",
];

function expectedIssueCode(corruption: Corruption): string {
  switch (corruption) {
    case "unknown_kind":
      return "PLOT_RENDER_UNKNOWN_KIND";
    case "unknown_scale":
      return "PLOT_RENDER_UNKNOWN_SCALE";
    case "unknown_field":
      return "PLOT_RENDER_INVALID_SPEC";
    case "fill_target_missing":
      return "PLOT_RENDER_FILL_TARGET_MISSING";
    case "fill_length_mismatch":
      return "FILL_BETWEEN_LENGTH_MISMATCH";
    case "fill_time_mismatch":
      return "FILL_BETWEEN_TIME_MISMATCH";
    case "log_non_positive":
      return "SCALE_BINDING_NON_POSITIVE_FOR_LOG";
  }
}

/**
 * One `TARGET` overlay carrying the seeded corruption, plus one always-valid
 * `GOOD_CONTROL` overlay in the same call -- proves isolation (a bad overlay
 * surfaces its own issue without dropping or corrupting an unrelated one),
 * not just that the corruption itself is rejected.
 */
function buildCorruptedCase(rng: () => number): {
  overlays: OverlayEntry[];
  overlaySeries: Map<string, OverlaySeriesByOutput>;
  overlayPlotSpecs: Map<string, Map<string, unknown>>;
  corruption: Corruption;
} {
  const corruption = CORRUPTIONS[Math.floor(rng() * CORRUPTIONS.length)]!;
  const n = 2 + Math.floor(rng() * 5);
  const needsPartner = corruption === "fill_length_mismatch" || corruption === "fill_time_mismatch";

  const targetOutputs: OverlayEntry["outputs"] = needsPartner
    ? [{ name: "value", series: "line" }, { name: "partner", series: "line" }]
    : [{ name: "value", series: "line" }];
  const targetOverlay = overlay("TARGET", "sub-pane", targetOutputs);
  const goodOverlay = overlay("GOOD_CONTROL", "sub-pane", [{ name: "value", series: "line" }]);

  const valuePoints = Array.from({ length: n }, (_, i) => ({
    time: i,
    value: corruption === "log_non_positive" ? -(1 + rng() * 5) : 10 + rng() * 40,
  }));

  const targetSeries = new Map<string, readonly { time: number; value: number }[]>([["value", series(valuePoints)]]);
  if (corruption === "fill_length_mismatch") {
    targetSeries.set("partner", series(valuePoints.slice(0, n - 1)));
  } else if (corruption === "fill_time_mismatch") {
    targetSeries.set(
      "partner",
      series(valuePoints.map((p) => ({ time: p.time + 0.5, value: p.value - 5 }))),
    );
  }

  let raw: Record<string, unknown>;
  switch (corruption) {
    case "unknown_kind":
      raw = { kind: "__nope__", scale: "own", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null };
      break;
    case "unknown_scale":
      raw = { kind: "line", scale: "__nope__", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null };
      break;
    case "unknown_field":
      raw = {
        kind: "line",
        scale: "own",
        default_pane: "separate",
        fill_between: null,
        color_rule: null,
        precision: null,
        legend_format: null,
        bogus_field: 1,
      };
      break;
    case "fill_target_missing":
      raw = { kind: "band", scale: "own", default_pane: "separate", fill_between: "ghost_partner", color_rule: null, precision: null, legend_format: null };
      break;
    case "fill_length_mismatch":
    case "fill_time_mismatch":
      raw = { kind: "band", scale: "own", default_pane: "separate", fill_between: "partner", color_rule: null, precision: null, legend_format: null };
      break;
    case "log_non_positive":
      raw = { kind: "line", scale: "log", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null };
      break;
  }

  const overlaySeries = new Map<string, OverlaySeriesByOutput>([
    ["TARGET", targetSeries],
    ["GOOD_CONTROL", new Map([["value", series(Array.from({ length: 3 }, (_, i) => ({ time: i, value: 50 + i })))]])],
  ]);
  const overlayPlotSpecs = new Map<string, Map<string, unknown>>([["TARGET", new Map([["value", raw]])]]);

  return { overlays: [targetOverlay, goodOverlay], overlaySeries, overlayPlotSpecs, corruption };
}

describe("failure injection: randomized malformed screen-level input fuzz (CH-15b buildPlotLayer)", () => {
  // buildPlotLayer has no I/O of its own -- it dispatches props a screen
  // hands it (overlaySeries/overlayPlotSpecs are still server-value-less
  // hooks per this file's own docstring, no live fetch/compute pipeline
  // exists yet) -- so mocked network/DB failure injection is structurally
  // impossible here, same reasoning the DEPTH_CH audit (task-2729) already
  // accepted for this leaf's pure-domain render-module siblings. This is the
  // closest analogue at the screen-wiring layer: a seeded fuzzer over every
  // malformed PlotSpec/series-pair shape a corrupted backend catalog entry or
  // a desynced client-side indicator compute could hand this dispatch loop,
  // proving each is surfaced as exactly one PlotLayerIssue for the offending
  // overlay -- never a thrown exception past buildPlotLayer, and never at the
  // cost of an unrelated, valid overlay sharing the same call (isolation).
  it("fail-closed with the correct issue code for 200 seeded corrupted overlay/spec/series combinations, never losing the unrelated good overlay", () => {
    const rng = mulberry32(0x2002);
    const ownScale = createPriceScale({ range: { min: -1000, max: 1000 }, height: 100 });
    const exercised = new Set<Corruption>();

    for (let i = 0; i < 200; i++) {
      const { overlays, overlaySeries, overlayPlotSpecs, corruption } = buildCorruptedCase(rng);
      exercised.add(corruption);

      const result = buildPlotLayer({ overlays, overlaySeries, overlayPlotSpecs, mainScale: MAIN_SCALE, ownScale, timeScale: TIME_SCALE });

      const targetIssues = result.issues.filter((issue) => issue.overlayId === "TARGET");
      expect(targetIssues, `corruption=${corruption} case ${i}`).toHaveLength(1);
      expect(targetIssues[0]!.code, `corruption=${corruption} case ${i}`).toBe(expectedIssueCode(corruption));
      expect(
        result.issues.some((issue) => issue.overlayId === "GOOD_CONTROL"),
        `corruption=${corruption} case ${i}: GOOD_CONTROL must never be dragged into an issue by TARGET's corruption`,
      ).toBe(false);
    }

    expect(exercised.size).toBe(CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk screen-level dispatch", () => {
  it("buildPlotLayer dispatches 50 overlays x 500 points (25,000 points across line/histogram/band kinds) within a fixed ms budget", () => {
    const overlays: OverlayEntry[] = [];
    const seriesEntries: [string, OverlaySeriesByOutput][] = [];
    const kinds = ["line", "histogram", "band"] as const;

    for (let i = 0; i < 50; i++) {
      const id = `PERF_${i}`;
      const kind = kinds[i % kinds.length]!;
      const outputs: OverlayEntry["outputs"] =
        kind === "band"
          ? [{ name: "upperband", series: "line" }, { name: "lowerband", series: "line" }]
          : [{ name: "value", series: kind === "histogram" ? "histogram" : "line" }];
      overlays.push(overlay(id, i % 2 === 0 ? "sub-pane" : "main-overlay", outputs));

      const points = Array.from({ length: 500 }, (_, t) => ({ time: t, value: 50 + Math.sin(t / 10 + i) * 20 }));
      const seriesByOutput: OverlaySeriesByOutput =
        kind === "band"
          ? new Map([
              ["upperband", series(points)],
              ["lowerband", series(points.map((p) => ({ ...p, value: p.value - 5 })))],
            ])
          : new Map([["value", series(points)]]);
      seriesEntries.push([id, seriesByOutput]);
    }

    const ownScale = createPriceScale({ range: { min: -100, max: 100 }, height: 100 });
    const start = performance.now();
    const result = buildPlotLayer({
      overlays,
      overlaySeries: new Map(seriesEntries),
      overlayPlotSpecs: new Map(),
      mainScale: MAIN_SCALE,
      ownScale,
      timeScale: TIME_SCALE,
    });
    const elapsedMs = performance.now() - start;

    expect(result.issues).toEqual([]);
    expect(result.nodes.length).toBeGreaterThan(0);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // per-overlay dispatch, spec derivation, or per-point projection
    // accidentally quadratic would blow well past this for 25,000 total
    // projected points spread across 50 overlays/~75 outputs.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: CH-15c fill_between error containment (task-2127)", () => {
  // Before task-2127, buildPlotLayer's catch clause only recognized
  // PlotRenderError/ScaleBindingError -- a FillBetweenError thrown by
  // fillBetween.ts's computeFillSegments (invoked from renderPlot's
  // band/cloud drawFill path) fell through uncaught, past this function's
  // try/catch entirely, and blanked every overlay in the call, not just the
  // offending one. This reproduces that exact regression as a `legacy`
  // variant (not the shipped module) to contrast red vs green.
  const style = { output: "value", color: "#fff", lineWidth: 1, visible: true } as const;
  const dummyTarget: PlotRenderTarget = {
    drawLine: () => {},
    drawHistogram: () => {},
    drawArea: () => {},
    drawPolygon: () => {},
    drawMarker: () => {},
  };

  function legacyDispatchNoFillBetweenCatch(input: PlotLayerInput): string[] {
    const rendered: string[] = [];
    for (const overlayEntry of input.overlays) {
      const seriesByOutput = input.overlaySeries.get(overlayEntry.id);
      if (!seriesByOutput) continue;
      const rawSpecs = input.overlayPlotSpecs.get(overlayEntry.id);
      for (const output of overlayEntry.outputs) {
        const points = seriesByOutput.get(output.name);
        if (!points) continue;
        const raw = rawSpecs?.get(output.name);
        const spec = raw !== undefined ? decodePlotSpec(raw) : deriveOverlayPlotSpec(overlayEntry.placement, overlayEntry.outputs, output);
        const projection = bindScale(spec.scale, { mainScale: input.mainScale, ownScale: input.ownScale, timeScale: input.timeScale });
        try {
          renderPlot(spec, output.name, seriesByOutput, projection, style, dummyTarget);
          rendered.push(`${overlayEntry.id}.${output.name}`);
        } catch (err) {
          // Pre-task-2127 gap: FillBetweenError is deliberately NOT listed
          // here, matching the historical bug -- it rethrows and escapes this
          // function entirely instead of becoming a PlotLayerIssue.
          if (err instanceof PlotRenderError || err instanceof ScaleBindingError) {
            continue;
          }
          throw err;
        }
      }
    }
    return rendered;
  }

  function buildScenario(): PlotLayerInput {
    const badBand = overlay("BAD_BAND", "main-overlay", [
      { name: "upperband", series: "line" },
      { name: "lowerband", series: "line" },
    ]);
    const goodLine = overlay("GOOD_LINE", "sub-pane", [{ name: "value", series: "line" }]);
    const points = [
      { time: 1, value: 100 },
      { time: 2, value: 101 },
      { time: 3, value: 102 },
    ];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([
      [
        "BAD_BAND",
        new Map([
          ["upperband", series(points)],
          // Mismatched length -- FILL_BETWEEN_LENGTH_MISMATCH, thrown from
          // deep inside renderPlot's band dispatch (drawFill -> computeFillSegments).
          ["lowerband", series(points.slice(0, 1))],
        ]),
      ],
      ["GOOD_LINE", new Map([["value", series(points)]])],
    ]);
    const overlayPlotSpecs = new Map<string, Map<string, unknown>>([
      [
        "BAD_BAND",
        new Map([
          [
            "upperband",
            { kind: "band", scale: "overlay", default_pane: "price", fill_between: "lowerband", color_rule: null, precision: null, legend_format: null },
          ],
        ]),
      ],
    ]);
    // BAD_BAND is processed first -- proves an uncaught FillBetweenError
    // blanks GOOD_LINE too, not just BAD_BAND.
    return { overlays: [badBand, goodLine], overlaySeries, overlayPlotSpecs, mainScale: MAIN_SCALE, ownScale: MAIN_SCALE, timeScale: TIME_SCALE };
  }

  it("red: the pre-task-2127 dispatch throws FillBetweenError past the whole loop, so GOOD_LINE never renders", () => {
    const input = buildScenario();
    expect(() => legacyDispatchNoFillBetweenCatch(input)).toThrow(FillBetweenError);
    expect(() => legacyDispatchNoFillBetweenCatch(input)).toThrow(/FILL_BETWEEN_LENGTH_MISMATCH/);
  });

  it("green: the shipped buildPlotLayer catches the FillBetweenError as one issue and still renders GOOD_LINE", () => {
    const input = buildScenario();
    const result = buildPlotLayer(input);

    expect(result.issues).toEqual([{ overlayId: "BAD_BAND", output: "upperband", code: "FILL_BETWEEN_LENGTH_MISMATCH" }]);
    const { container } = render(<svg>{result.nodes}</svg>);
    expect(container.querySelectorAll("polyline").length).toBeGreaterThanOrEqual(1);
  });
});
