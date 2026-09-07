// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { VendorChart } from "../../core/klinecharts";
import { getPaneLayers, LayerError, supportsOffscreenCanvas } from "../layers";

function fakeChart(dom: HTMLElement | null): VendorChart {
  return { getDom: () => dom } as unknown as VendorChart;
}

describe("getPaneLayers", () => {
  it("locates the vendor main/overlay canvas pair inside the pane's widget container", () => {
    const container = document.createElement("div");
    const main = document.createElement("canvas");
    const overlay = document.createElement("canvas");
    container.appendChild(main);
    container.appendChild(overlay);

    const layers = getPaneLayers(fakeChart(container), "candle_pane");

    expect(layers.paneId).toBe("candle_pane");
    expect(layers.main).toBe(main);
    expect(layers.overlay).toBe(overlay);
  });

  it("throws LAYER_PANE_NOT_FOUND when the container has no canvases", () => {
    const container = document.createElement("div");
    expect(() => getPaneLayers(fakeChart(container), "candle_pane")).toThrow(LayerError);
    try {
      getPaneLayers(fakeChart(container), "candle_pane");
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(LayerError);
      expect((err as LayerError).code).toBe("LAYER_PANE_NOT_FOUND");
    }
  });

  it("throws LAYER_PANE_NOT_FOUND when the vendor has no dom for that pane at all", () => {
    expect(() => getPaneLayers(fakeChart(null), "missing_pane")).toThrow(LayerError);
  });

  it("reports offscreenCapable consistently with supportsOffscreenCanvas()", () => {
    const container = document.createElement("div");
    container.appendChild(document.createElement("canvas"));
    const layers = getPaneLayers(fakeChart(container), "candle_pane");
    expect(layers.offscreenCapable).toBe(supportsOffscreenCanvas());
  });
});
