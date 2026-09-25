import { describe, expect, it } from "vitest";
import { decodePlotSpec, renderPlot } from "../plotRenderers";
import { createRng, genSeries, genValidPlotSpec } from "./arbitraries";
import { IDENTITY_PROJECTION, POINTS, VISIBLE_STYLE, recordingTarget, spec } from "./plotRenderers.helpers";

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: 300 seeded random valid PlotSpecs round-trip through decodePlotSpec unchanged", () => {
    const rng = createRng(0xd3d3);
    for (let i = 0; i < 300; i++) {
      const candidate = genValidPlotSpec(rng);
      expect(decodePlotSpec(candidate), `case ${i}`).toEqual(candidate);
    }
  });

  it("property: renderPlot never throws for a valid spec paired with matching series, and always dispatches exactly once", () => {
    const rng = createRng(0xd3d4);
    for (let i = 0; i < 300; i++) {
      const candidate = genValidPlotSpec(rng);
      const points = genSeries(rng, 5);
      const seriesByOutput = new Map([["value", points], ["partner", points]]);
      const target = recordingTarget();
      expect(
        () => renderPlot(candidate, "value", seriesByOutput, IDENTITY_PROJECTION, VISIBLE_STYLE, target),
        `case ${i}: ${JSON.stringify(candidate)}`,
      ).not.toThrow();
      expect(target.calls.length, `case ${i}`).toBeGreaterThanOrEqual(1);
    }
  });

  it("multi-instance isolation (D3): two independently-rendered targets interleaved never leak draw calls into each other", () => {
    const targetA = recordingTarget();
    const targetB = recordingTarget();
    const seriesA = new Map([["value", POINTS]]);
    const seriesB = new Map([["value", [{ time: 1, value: 999 }]]]);

    renderPlot(spec({ kind: "line" }), "value", seriesA, IDENTITY_PROJECTION, VISIBLE_STYLE, targetA);
    renderPlot(spec({ kind: "histogram" }), "value", seriesB, IDENTITY_PROJECTION, VISIBLE_STYLE, targetB);
    renderPlot(spec({ kind: "marker" }), "value", seriesA, IDENTITY_PROJECTION, VISIBLE_STYLE, targetA);
    renderPlot(spec({ kind: "area" }), "value", seriesB, IDENTITY_PROJECTION, VISIBLE_STYLE, targetB);

    expect(targetA.calls).toEqual(["drawLine", "drawMarker"]);
    expect(targetB.calls).toEqual(["drawHistogram", "drawArea"]);
  });

  it("adversarial: an extreme but finite precision value still decodes without throwing", () => {
    for (const precision of [0, 1e6, Number.MAX_SAFE_INTEGER]) {
      expect(decodePlotSpec({ kind: "line", scale: "own", default_pane: "separate", precision }).precision).toBe(precision);
    }
  });
});
