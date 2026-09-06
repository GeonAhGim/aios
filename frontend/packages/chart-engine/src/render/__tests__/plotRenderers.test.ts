import { describe, expect, it } from "vitest";
import type { IndicatorStyleOutput } from "../../plugins/indicatorPlugin";
import { PlotRenderError, type PlotRenderTarget, type PlotSpec, decodePlotSpec, renderPlot } from "../plotRenderers";
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

describe("DoD: a brand-new indicator's PlotSpec renders without touching plotRenderers.ts", () => {
  it("injecting one never-before-seen spec (new output name, existing kind) triggers a render call", () => {
    const target = recordingTarget();
    const brandNewSpec = spec({ kind: "marker", scale: "overlay" });
    const brandNewOutput = "totally_new_indicator_output_xyz";
    renderPlot(brandNewSpec, brandNewOutput, new Map([[brandNewOutput, POINTS]]), IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    expect(target.calls).toEqual(["drawMarker"]);
  });
});
