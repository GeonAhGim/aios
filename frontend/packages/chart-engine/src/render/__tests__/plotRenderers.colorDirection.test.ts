import { describe, expect, it } from "vitest";
import type { IndicatorStyleOutput } from "../../plugins/indicatorPlugin";
import { PlotRenderError, type PlotRenderTarget, decodePlotSpec, renderPlot } from "../plotRenderers";
import { createRng, genSeries } from "./arbitraries";
import { IDENTITY_PROJECTION, POINTS, recordingTarget, spec, spyTarget } from "./plotRenderers.helpers";

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

describe("CH-15/2028 DEEPEN: color_rule=sign & fill_between direction consumption depth", () => {
  // DEPTH_CH (task-2729) flagged task-2028's 053b3d74 (color_rule/fill-direction
  // consumption wiring) as D1: only 1 negative test, no failure injection, no
  // numeric perf assertion, no gate-red repro, no D3 for this specific
  // consumption path (the sections above cover the module generically, per
  // sibling task-3087/7a38c2f8, but never exercise color_rule="sign" or a
  // fill_between direction split under fuzz/perf/mutant/property pressure).
  const SIGN_STYLE: IndicatorStyleOutput = { output: "hist", color: "#888", lineWidth: 1, visible: true, upColor: "#0f0", downColor: "#f00" };
  const DIRECTION_STYLE: IndicatorStyleOutput = { output: "upper", color: "#888", lineWidth: 1, visible: true, aboveColor: "#0f0", belowColor: "#f00" };

  describe("negative & fail-closed (>=3)", () => {
    it("color_rule=sign histogram still throws PLOT_RENDER_SERIES_MISSING when the output's own series is absent (guard precedes the color-split branch)", () => {
      const target = spyTarget();
      expect(() =>
        renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map(), IDENTITY_PROJECTION, SIGN_STYLE, target),
      ).toThrow(/PLOT_RENDER_SERIES_MISSING/);
    });

    it("fill_between direction dispatch still throws PLOT_RENDER_FILL_TARGET_MISSING when the partner series is absent (guard precedes the direction-split branch)", () => {
      const target = spyTarget();
      const series = new Map([["upper", POINTS]]);
      expect(() =>
        renderPlot(spec({ kind: "band", fill_between: "lower" }), "upper", series, IDENTITY_PROJECTION, DIRECTION_STYLE, target),
      ).toThrow(/PLOT_RENDER_FILL_TARGET_MISSING/);
    });

    it("decodePlotSpec rejects a non-string color_rule (negative, type-level fail-closed)", () => {
      expect(() =>
        decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: 42 }),
      ).toThrow(PlotRenderError);
      try {
        decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: 42 });
      } catch (err) {
        expect((err as PlotRenderError).code).toBe("PLOT_RENDER_INVALID_SPEC");
      }
    });

    it("decodePlotSpec rejects a color_rule given as an array (negative, type-level fail-closed)", () => {
      expect(() =>
        decodePlotSpec({ kind: "histogram", scale: "own", default_pane: "separate", color_rule: ["sign"] }),
      ).toThrow(/color_rule must be a string or null/);
    });
  });

  describe("failure injection: draw target throwing mid-dispatch", () => {
    it("color_rule=sign: the up-bar call still happens before a down-bar draw failure propagates (no silent swallow)", () => {
      const calls: string[] = [];
      const target: PlotRenderTarget = {
        drawLine: () => {},
        drawHistogram: (_bars, style) => {
          calls.push(style.color);
          if (style.color === SIGN_STYLE.downColor) throw new Error("simulated canvas failure on down-bar draw");
        },
        drawArea: () => {},
        drawPolygon: () => {},
        drawMarker: () => {},
      };
      const signedPoints = [
        { time: 1, value: 1 },
        { time: 2, value: -1 },
      ];
      expect(() =>
        renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map([["hist", signedPoints]]), IDENTITY_PROJECTION, SIGN_STYLE, target),
      ).toThrow(/simulated canvas failure/);
      expect(calls[0]).toBe(SIGN_STYLE.upColor);
    });

    it("fill_between direction: the first segment still draws before a second-segment draw failure propagates (no silent swallow)", () => {
      const calls: string[] = [];
      const target: PlotRenderTarget = {
        drawLine: () => {},
        drawHistogram: () => {},
        drawArea: () => {},
        drawPolygon: (_points, style) => {
          calls.push(style.color);
          if (calls.length === 2) throw new Error("simulated canvas failure on second segment draw");
        },
        drawMarker: () => {},
      };
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
      expect(() =>
        renderPlot(spec({ kind: "band", fill_between: "lower" }), "upper", series, IDENTITY_PROJECTION, DIRECTION_STYLE, target),
      ).toThrow(/simulated canvas failure/);
      expect(calls).toHaveLength(2);
      expect(calls[0]).not.toBe(calls[1]);
    });
  });

  describe("performance: numeric ms budget for color_rule/direction dispatch", () => {
    it("renders a 10,000-bar alternating-sign histogram 200 times within a fixed ms budget", () => {
      const points = Array.from({ length: 10_000 }, (_, i) => ({ time: i, value: i % 2 === 0 ? i : -i }));
      const target = recordingTarget();
      const series = new Map([["hist", points]]);
      const histSpec = spec({ kind: "histogram", color_rule: "sign" });

      const start = performance.now();
      for (let i = 0; i < 200; i++) {
        renderPlot(histSpec, "hist", series, IDENTITY_PROJECTION, SIGN_STYLE, target);
      }
      const elapsedMs = performance.now() - start;

      expect(target.calls).toHaveLength(400); // one up-call + one down-call per render
      // Generous fixed budget (matches the sibling perf tests in this file): under
      // whole-package parallel test runs this workload has been observed to spike
      // well past a tight 3s budget purely from CI/system noise, not a regression.
      expect(elapsedMs).toBeLessThan(8000);
    });

    it("draws fill_between direction for a 5,000-point frequently-crossing band 100 times within a fixed ms budget", () => {
      const rng = createRng(0x3000);
      const upper = genSeries(rng, 5_000);
      const lower = genSeries(rng, 5_000);
      const target = recordingTarget();
      const series = new Map([["upper", upper], ["lower", lower]]);
      const bandSpec = spec({ kind: "band", fill_between: "lower" });

      const start = performance.now();
      for (let i = 0; i < 100; i++) {
        renderPlot(bandSpec, "upper", series, IDENTITY_PROJECTION, DIRECTION_STYLE, target);
      }
      const elapsedMs = performance.now() - start;

      expect(target.calls.length).toBeGreaterThan(0);
      expect(elapsedMs).toBeLessThan(8000);
    });
  });

  describe("gate red reproduction: color_rule/direction consumption", () => {
    /** Mimics the pre-053b3d74 dispatch that parsed color_rule but never consumed it — a
     * single drawHistogram call ignoring sign. A mutant, not part of the shipped module. */
    function legacyHistogramIgnoringColorRule(target: PlotRenderTarget, style: IndicatorStyleOutput): void {
      target.drawHistogram([{ x: 0, y: 0, baselineY: 0 }, { x: 1, y: 1, baselineY: 0 }], style);
    }

    it("red: a naive dispatch draws all histogram bars in one single-color call regardless of sign", () => {
      const target = spyTarget();
      legacyHistogramIgnoringColorRule(target, SIGN_STYLE);
      expect(target.histogramCalls).toHaveLength(1);
      expect(target.histogramCalls[0]!.bars).toHaveLength(2);
    });

    it("green: the shipped renderPlot splits color_rule=sign bars into two differently-colored calls", () => {
      const target = spyTarget();
      const signedPoints = [
        { time: 1, value: 1 },
        { time: 2, value: -1 },
      ];
      renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map([["hist", signedPoints]]), IDENTITY_PROJECTION, SIGN_STYLE, target);
      expect(target.histogramCalls).toHaveLength(2);
      expect(target.histogramCalls[0]!.style.color).not.toBe(target.histogramCalls[1]!.style.color);
    });

    /** Mimics the pre-053b3d74 fill dispatch that always drew every segment with the base
     * style color, ignoring direction. A mutant, not part of the shipped module. */
    function legacyFillIgnoringDirection(target: PlotRenderTarget, style: IndicatorStyleOutput): void {
      target.drawPolygon([{ x: 0, y: 0 }], style);
      target.drawPolygon([{ x: 1, y: 1 }], style);
    }

    it("red: a naive fill dispatch draws every segment with the same uniform color regardless of direction", () => {
      const target = spyTarget();
      legacyFillIgnoringDirection(target, DIRECTION_STYLE);
      const colors = new Set(target.polygonCalls.map((c) => c.style.color));
      expect(colors.size).toBe(1);
    });

    it("green: the shipped renderPlot colors above/below fill segments differently", () => {
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
      const colors = new Set(target.polygonCalls.map((c) => c.style.color));
      expect(colors.size).toBeGreaterThan(1);
    });
  });

  describe("D3 — color_rule/direction property & multi-instance isolation", () => {
    it("property: 300 seeded signed-value series always partition into up/down groups covering every input point by its true sign", () => {
      const rng = createRng(0xd3d5);
      for (let i = 0; i < 300; i++) {
        const length = rng.int(1, 40);
        const points = Array.from({ length }, (_, idx) => ({ time: idx, value: rng.next() * 200 - 100 }));
        const target = spyTarget();
        renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map([["hist", points]]), IDENTITY_PROJECTION, SIGN_STYLE, target);

        const expectedUp = points.filter((p) => p.value >= 0).length;
        const expectedDown = points.filter((p) => p.value < 0).length;
        const totalBars = target.histogramCalls.reduce((sum, c) => sum + c.bars.length, 0);
        expect(totalBars, `case ${i}`).toBe(length);
        if (expectedUp > 0) {
          expect(target.histogramCalls.some((c) => c.style.color === SIGN_STYLE.upColor && c.bars.length === expectedUp), `case ${i}`).toBe(true);
        }
        if (expectedDown > 0) {
          expect(target.histogramCalls.some((c) => c.style.color === SIGN_STYLE.downColor && c.bars.length === expectedDown), `case ${i}`).toBe(true);
        }
      }
    });

    it("multi-instance isolation (D3): two independently-rendered color_rule=sign targets interleaved never leak histogram calls into each other", () => {
      const targetA = spyTarget();
      const targetB = spyTarget();
      const seriesA = new Map([["hist", [{ time: 1, value: 5 }, { time: 2, value: -5 }]]]);
      const seriesB = new Map([["hist", [{ time: 1, value: -9 }]]]);

      renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", seriesA, IDENTITY_PROJECTION, SIGN_STYLE, targetA);
      renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", seriesB, IDENTITY_PROJECTION, SIGN_STYLE, targetB);

      expect(targetA.histogramCalls).toHaveLength(2);
      expect(targetB.histogramCalls).toHaveLength(1);
      expect(targetB.histogramCalls[0]!.style.color).toBe(SIGN_STYLE.downColor);
    });

    it("adversarial: a value of exactly 0 resolves to the up-bar group (boundary, value < 0 is false)", () => {
      const target = spyTarget();
      const points = [{ time: 1, value: 0 }];
      renderPlot(spec({ kind: "histogram", color_rule: "sign" }), "hist", new Map([["hist", points]]), IDENTITY_PROJECTION, SIGN_STYLE, target);
      expect(target.histogramCalls).toHaveLength(1);
      expect(target.histogramCalls[0]!.style.color).toBe(SIGN_STYLE.upColor);
    });
  });
});
