import { describe, expect, it } from "vitest";
import { renderPlot } from "../plotRenderers";
import { IDENTITY_PROJECTION, POINTS, VISIBLE_STYLE, recordingTarget, spec } from "./plotRenderers.helpers";

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
    renderPlot(spec(), "value", new Map([["value", POINTS]]), IDENTITY_PROJECTION, { ...VISIBLE_STYLE, visible: false }, target);
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
