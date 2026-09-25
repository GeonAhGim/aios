import { describe, expect, it } from "vitest";
import type { OverlayOutput } from "../../indicators/overlayRegistry";
import { PlotRenderError, decodePlotSpec, deriveOverlayPlotSpec } from "../plotRenderers";
import { spec } from "./plotRenderers.helpers";

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
