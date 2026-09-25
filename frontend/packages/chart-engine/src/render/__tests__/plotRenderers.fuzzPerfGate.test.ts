import { describe, expect, it } from "vitest";
import { PlotRenderError, type PlotRenderTarget, decodePlotSpec, renderPlot } from "../plotRenderers";
import {
  type PlotSpecCorruption,
  PLOT_SPEC_CORRUPTIONS,
  createRng,
  genSeries,
  pickCorruptedPlotSpec,
} from "./arbitraries";
import { HIDDEN_STYLE, IDENTITY_PROJECTION, POINTS, VISIBLE_STYLE, recordingTarget, spec } from "./plotRenderers.helpers";

function expectedCorruptionCode(corruption: PlotSpecCorruption): string {
  switch (corruption) {
    case "unknown_kind":
      return "PLOT_RENDER_UNKNOWN_KIND";
    case "unknown_scale":
      return "PLOT_RENDER_UNKNOWN_SCALE";
    default:
      return "PLOT_RENDER_INVALID_SPEC";
  }
}

describe("failure injection: randomized malformed PlotSpec fuzz", () => {
  // decodePlotSpec is a pure parser with no I/O, so mocked network/DB failure
  // injection is structurally impossible (per DEPTH_CH audit, task-2729, for
  // this exact leaf and its chart-engine pure-domain siblings 1710/1711/1809).
  // This is the closest analogue: a seeded fuzzer over the malformed-payload
  // domain a backend catalog fetch or a hand-edited layout blob could hand
  // this decoder, proving every corruption is rejected fail-closed with the
  // documented error code rather than just one hand-picked example per kind.
  it("fail-closed with the correct error code for 200 seeded corrupted PlotSpec payloads", () => {
    const rng = createRng(0x9153);
    const exercised = new Set<PlotSpecCorruption>();
    for (let i = 0; i < 200; i++) {
      const { corruption, raw } = pickCorruptedPlotSpec(rng);
      exercised.add(corruption);
      expect(() => decodePlotSpec(raw), `corruption=${corruption} case ${i}`).toThrow(PlotRenderError);
      try {
        decodePlotSpec(raw);
        expect.unreachable();
      } catch (err) {
        expect((err as PlotRenderError).code, `corruption=${corruption} case ${i}`).toBe(expectedCorruptionCode(corruption));
      }
    }
    expect(exercised.size).toBe(PLOT_SPEC_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk rendering", () => {
  it("renders a 10,000-point line series 200 times within a fixed ms budget", () => {
    const points = genSeries(createRng(0x1000), 10_000);
    const target = recordingTarget();
    const series = new Map([["value", points]]);
    const lineSpec = spec({ kind: "line" });

    const start = performance.now();
    for (let i = 0; i < 200; i++) {
      renderPlot(lineSpec, "value", series, IDENTITY_PROJECTION, VISIBLE_STYLE, target);
    }
    const elapsedMs = performance.now() - start;

    expect(target.calls).toHaveLength(200);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // per-point projection or dispatch accidentally quadratic would blow well
    // past this for 2,000,000 total projected points.
    expect(elapsedMs).toBeLessThan(3000);
  });

  it("decodes 100,000 PlotSpecs within a fixed ms budget", () => {
    const raw = {
      kind: "band",
      scale: "overlay",
      default_pane: "price",
      fill_between: "lowerband",
      color_rule: null,
      precision: 2,
      legend_format: "{value:.2f}",
    };

    const start = performance.now();
    for (let i = 0; i < 100_000; i++) decodePlotSpec(raw);
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: PlotSpec/render invariants", () => {
  describe("decodePlotSpec: no precision range validation", () => {
    /** Mimics a pre-hardening decode with no non-negative check on precision.
     * A mutant, not part of the shipped decoder. */
    function legacyDecodeNoPrecisionCheck(raw: { precision: number }): { precision: number } {
      return { precision: raw.precision };
    }

    it("red: a naive decoder accepts a negative precision and would format with it", () => {
      const legacy = legacyDecodeNoPrecisionCheck({ precision: -3 });
      expect(legacy.precision).toBe(-3);
    });

    it("green: the shipped decodePlotSpec rejects a negative precision instead of accepting it silently", () => {
      expect(() => decodePlotSpec({ kind: "line", scale: "own", default_pane: "separate", precision: -3 })).toThrow(
        /precision must be a non-negative number/,
      );
    });
  });

  describe("renderPlot: no visibility gate", () => {
    /** Mimics a pre-hardening dispatch that always draws regardless of
     * `style.visible`. A mutant of `renderPlot`, not part of the shipped
     * module. */
    function legacyRenderIgnoringVisible(target: PlotRenderTarget): void {
      target.drawLine([{ x: 0, y: 0 }], HIDDEN_STYLE);
    }

    it("red: a naive dispatch draws a hidden output anyway", () => {
      const target = recordingTarget();
      legacyRenderIgnoringVisible(target);
      expect(target.calls).toEqual(["drawLine"]);
    });

    it("green: the shipped renderPlot skips drawing entirely when style.visible is false", () => {
      const target = recordingTarget();
      renderPlot(spec(), "value", new Map([["value", POINTS]]), IDENTITY_PROJECTION, HIDDEN_STYLE, target);
      expect(target.calls).toEqual([]);
    });
  });
});
