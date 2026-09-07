import { describe, expect, it } from "vitest";
import type { OverlayOutput } from "../../indicators/overlayRegistry";
import type { IndicatorStyleOutput } from "../../plugins/indicatorPlugin";
import {
  PlotRenderError,
  type HistogramBar,
  type PlotRenderTarget,
  type PlotSpec,
  decodePlotSpec,
  deriveOverlayPlotSpec,
  renderPlot,
} from "../plotRenderers";
import type { PlotProjection } from "../scaleBinding";

const IDENTITY_PROJECTION: PlotProjection = { timeToX: (t) => t, priceToY: (v) => v };

const VISIBLE_STYLE: IndicatorStyleOutput = { output: "value", color: "#fff", lineWidth: 1, visible: true };
const HIDDEN_STYLE: IndicatorStyleOutput = { ...VISIBLE_STYLE, visible: false };

function spec(overrides: Partial<PlotSpec> = {}): PlotSpec {
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

function recordingTarget(): PlotRenderTarget & { calls: string[] } {
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

const POINTS = [
  { time: 1, value: 10 },
  { time: 2, value: 12 },
];

/** Unlike `recordingTarget`, captures the actual `style` object per call so a test can compare
 * arguments structurally (color A != color B) instead of asserting a hardcoded color literal. */
function spyTarget(): PlotRenderTarget & {
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

describe("decodePlotSpec", () => {
  it("decodes a full backend-shaped PlotSpec verbatim (snake_case fields)", () => {
    const decoded = decodePlotSpec({
      kind: "band",
      scale: "overlay",
      default_pane: "price",
      fill_between: "lowerband",
      color_rule: null,
      precision: 2,
      legend_format: "{value:.2f}",
    });
    expect(decoded).toEqual({
      kind: "band",
      scale: "overlay",
      default_pane: "price",
      fill_between: "lowerband",
      color_rule: null,
      precision: 2,
      legend_format: "{value:.2f}",
    });
  });

  it("rejects an unknown kind (negative)", () => {
    expect(() => decodePlotSpec({ kind: "pie", scale: "own", default_pane: "separate" })).toThrow(PlotRenderError);
    try {
      decodePlotSpec({ kind: "pie", scale: "own", default_pane: "separate" });
    } catch (err) {
      expect((err as PlotRenderError).code).toBe("PLOT_RENDER_UNKNOWN_KIND");
    }
  });

  it("rejects an unknown scale (negative)", () => {
    expect(() => decodePlotSpec({ kind: "line", scale: "sqrt", default_pane: "separate" })).toThrow(PlotRenderError);
    try {
      decodePlotSpec({ kind: "line", scale: "sqrt", default_pane: "separate" });
    } catch (err) {
      expect((err as PlotRenderError).code).toBe("PLOT_RENDER_UNKNOWN_SCALE");
    }
  });

  it("rejects an unknown field (fail-closed, refuses to silently drop it)", () => {
    expect(() =>
      decodePlotSpec({ kind: "line", scale: "own", default_pane: "separate", made_up_field: 1 }),
    ).toThrow(/unknown field/);
  });

  it("accepts color_rule=null (current single-color behavior, regression)", () => {
    expect(decodePlotSpec({ kind: "line", scale: "own", default_pane: "separate", color_rule: null }).color_rule).toBeNull();
  });

  it("accepts the only color_rule value the backend actually emits (specs_talib.py hist output)", () => {
    expect(decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: "sign" }).color_rule).toBe("sign");
  });

  it("rejects a color_rule the backend never emits (negative, fail-closed — no silent single-color fallback)", () => {
    expect(() =>
      decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: "rainbow" }),
    ).toThrow(PlotRenderError);
    try {
      decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: "rainbow" });
    } catch (err) {
      expect((err as PlotRenderError).code).toBe("PLOT_RENDER_INVALID_SPEC");
    }
  });
});

describe("renderPlot: kind dispatch", () => {
  it.each([
    ["line", "drawLine"],
    ["area", "drawArea"],
    ["histogram", "drawHistogram"],
    ["marker", "drawMarker"],
  ] as const)("kind=%s calls target.%s", (kind, expectedCall) => {
    const target = recordingTarget();
    renderPlot(spec({ kind }), "value", new Map([["value", POINTS]]), IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual([expectedCall]);
  });

  it("kind=band draws the line and fills against its fill_between partner", () => {
    const target = recordingTarget();
    const series = new Map([
      ["upperband", POINTS],
      ["lowerband", [
        { time: 1, value: 5 },
        { time: 2, value: 6 },
      ]],
    ]);
    renderPlot(spec({ kind: "band", fill_between: "lowerband" }), "upperband", series, IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual(["drawLine", "drawPolygon"]);
  });

  it("kind=cloud behaves like band (line + fill)", () => {
    const target = recordingTarget();
    const series = new Map([
      ["spanA", POINTS],
      ["spanB", [
        { time: 1, value: 5 },
        { time: 2, value: 6 },
      ]],
    ]);
    renderPlot(spec({ kind: "cloud", fill_between: "spanB" }), "spanA", series, IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual(["drawLine", "drawPolygon"]);
  });

  it("band with no fill_between just draws the line (no partner required)", () => {
    const target = recordingTarget();
    renderPlot(spec({ kind: "band", fill_between: null }), "value", new Map([["value", POINTS]]), IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual(["drawLine"]);
  });

  it("skips drawing entirely when style.visible is false", () => {
    const target = recordingTarget();
    renderPlot(spec(), "value", new Map([["value", POINTS]]), IDENTITY_PROJECTION, HIDDEN_STYLE, target);
    expect(target.calls).toEqual([]);
  });

  it("rejects rendering when the output's own series is missing (negative)", () => {
    const target = recordingTarget();
    expect(() => renderPlot(spec(), "value", new Map(), IDENTITY_PROJECTION, VISIBLE_STYLE, target)).toThrow(
      /PLOT_RENDER_SERIES_MISSING/,
    );
  });

  it("rejects rendering a band whose fill_between partner series is absent (negative)", () => {
    const target = recordingTarget();
    const series = new Map([["upperband", POINTS]]);
    expect(() =>
      renderPlot(spec({ kind: "band", fill_between: "lowerband" }), "upperband", series, IDENTITY_PROJECTION, VISIBLE_STYLE, target),
    ).toThrow(/PLOT_RENDER_FILL_TARGET_MISSING/);
  });
});

describe("renderPlot: color_rule=sign consumption (histogram)", () => {
  const SIGN_STYLE: IndicatorStyleOutput = { output: "hist", color: "#888", lineWidth: 1, visible: true, upColor: "#0f0", downColor: "#f00" };
  const SIGNED_POINTS = [
    { time: 1, value: 1 },
    { time: 2, value: -1 },
  ];

  it("colors a positive bar and a negative bar differently (spy on the actual draw args, not a hardcoded color literal)", () => {
    const target = spyTarget();
    renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map([["hist", SIGNED_POINTS]]), IDENTITY_PROJECTION, SIGN_STYLE, target);

    expect(target.histogramCalls).toHaveLength(2);
    const [firstCall, secondCall] = target.histogramCalls;
    expect(firstCall!.style.color).not.toBe(secondCall!.style.color);
    expect(firstCall!.bars).toHaveLength(1);
    expect(secondCall!.bars).toHaveLength(1);
  });

  it("color_rule=null keeps the current single-call, single-color behavior (regression)", () => {
    const target = spyTarget();
    renderPlot(spec({ kind: "histogram", color_rule: null }), "hist", new Map([["hist", SIGNED_POINTS]]), IDENTITY_PROJECTION, SIGN_STYLE, target);

    expect(target.histogramCalls).toHaveLength(1);
    expect(target.histogramCalls[0]!.bars).toHaveLength(2);
    expect(target.histogramCalls[0]!.style.color).toBe(SIGN_STYLE.color);
  });
});

describe("renderPlot: fill_between direction consumption (band/cloud)", () => {
  const DIRECTION_STYLE: IndicatorStyleOutput = { output: "upper", color: "#888", lineWidth: 1, visible: true, aboveColor: "#0f0", belowColor: "#f00" };

  it("above and below segments are passed to drawPolygon with different colors (currently the same style)", () => {
    const target = spyTarget();
    const series = new Map([
      ["upper", [
        { time: 1, value: 100 },
        { time: 2, value: 120 },
        { time: 3, value: 90 },
      ]],
      ["lower", [
        { time: 1, value: 110 },
        { time: 2, value: 110 },
        { time: 3, value: 110 },
      ]],
    ]);
    renderPlot(spec({ kind: "band", fill_between: "lower" }), "upper", series, IDENTITY_PROJECTION, DIRECTION_STYLE, target);

    expect(target.polygonCalls.length).toBeGreaterThanOrEqual(2);
    const colors = new Set(target.polygonCalls.map((c) => c.style.color));
    expect(colors.size).toBeGreaterThan(1);
  });

  it("a segment with direction=equal is skipped (not drawn), fixed by this test", () => {
    const target = spyTarget();
    const flat = [
      { time: 1, value: 100 },
      { time: 2, value: 100 },
    ];
    const series = new Map([
      ["upper", flat],
      ["lower", flat],
    ]);
    renderPlot(spec({ kind: "cloud", fill_between: "lower" }), "upper", series, IDENTITY_PROJECTION, DIRECTION_STYLE, target);

    expect(target.polygonCalls).toHaveLength(0);
  });
});

describe("deriveOverlayPlotSpec", () => {
  const value: OverlayOutput = { name: "value", series: "line" };
  const hist: OverlayOutput = { name: "hist", series: "histogram" };
  const upperband: OverlayOutput = { name: "upperband", series: "line" };
  const middleband: OverlayOutput = { name: "middleband", series: "line" };
  const lowerband: OverlayOutput = { name: "lowerband", series: "line" };

  it("main-overlay line output gets scale=overlay/default_pane=price", () => {
    expect(deriveOverlayPlotSpec("main-overlay", [value], value)).toEqual(
      spec({ kind: "line", scale: "overlay", default_pane: "price" }),
    );
  });

  it("sub-pane line output gets scale=own/default_pane=separate", () => {
    expect(deriveOverlayPlotSpec("sub-pane", [value], value)).toEqual(
      spec({ kind: "line", scale: "own", default_pane: "separate" }),
    );
  });

  it("a histogram-series output derives kind=histogram", () => {
    expect(deriveOverlayPlotSpec("sub-pane", [hist], hist)).toEqual(
      spec({ kind: "histogram", scale: "own", default_pane: "separate" }),
    );
  });

  it("an upperband/lowerband pair derives kind=band with fill_between only on upperband (BBANDS-shaped)", () => {
    const outputs = [upperband, middleband, lowerband];
    expect(deriveOverlayPlotSpec("main-overlay", outputs, upperband)).toEqual(
      spec({ kind: "band", scale: "overlay", default_pane: "price", fill_between: "lowerband" }),
    );
    expect(deriveOverlayPlotSpec("main-overlay", outputs, lowerband)).toEqual(
      spec({ kind: "band", scale: "overlay", default_pane: "price", fill_between: null }),
    );
    expect(deriveOverlayPlotSpec("main-overlay", outputs, middleband)).toEqual(
      spec({ kind: "line", scale: "overlay", default_pane: "price" }),
    );
  });

  it("every derived spec decodes cleanly through decodePlotSpec (shape stays valid)", () => {
    for (const output of [value, hist, upperband, lowerband]) {
      expect(() => decodePlotSpec(deriveOverlayPlotSpec("sub-pane", [output], output))).not.toThrow();
    }
  });
});

describe("DoD: a brand-new indicator's PlotSpec renders without touching plotRenderers.ts", () => {
  it("injecting one never-before-seen spec (new output name, existing kind) triggers a render call", () => {
    const target = recordingTarget();
    const brandNewSpec = spec({ kind: "marker", scale: "overlay" });
    const brandNewOutput = "totally_new_indicator_output_xyz";
    renderPlot(brandNewSpec, brandNewOutput, new Map([[brandNewOutput, POINTS]]), IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual(["drawMarker"]);
  });
});
