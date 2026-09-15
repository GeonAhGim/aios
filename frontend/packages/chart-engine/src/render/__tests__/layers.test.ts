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

// --- DEEPEN 2666 (docs/audit/DEPTH_CH.md, task-1959/task-3096 covered the
// density-ratchet half of CH-19b but layers.ts itself was left at D1: only 2
// negative tests, no failure injection, no numeric performance assertion, no
// gate-red reproduction. layers.ts is untouched; the axes below are
// test-only additions bringing it to the ADR-2026-09-09-C D2 floor.

describe("DEEPEN 2666 — malformed vendor response failure injection (a real chart.getDom() call can throw or return a shape outside its declared Nullable<HTMLElement> contract, e.g. mid-dispose)", () => {
  it("propagates an error thrown by chart.getDom() instead of swallowing it (vendor invariant violation, e.g. a disposed chart) (negative, failure injection)", () => {
    const throwingChart = {
      getDom: () => {
        throw new Error("chart disposed");
      },
    } as unknown as VendorChart;

    expect(() => getPaneLayers(throwingChart, "candle_pane")).toThrow("chart disposed");
  });

  it("throws an unguarded TypeError (not LayerError) when the vendor returns a malformed non-null container without querySelectorAll -- findCanvases only guards the === null case, not other malformed shapes (negative, documents current gap)", () => {
    const malformedContainer = {} as unknown as HTMLElement;
    const chart = { getDom: () => malformedContainer } as unknown as VendorChart;

    expect(() => getPaneLayers(chart, "candle_pane")).toThrow(TypeError);
    try {
      getPaneLayers(chart, "candle_pane");
      expect.unreachable();
    } catch (err) {
      expect(err).not.toBeInstanceOf(LayerError);
    }
  });

  it("throws LAYER_PANE_NOT_FOUND for an empty paneId string rather than resolving some default pane (negative)", () => {
    const container = document.createElement("div");
    expect(() => getPaneLayers(fakeChart(container), "")).toThrow(LayerError);
    try {
      getPaneLayers(fakeChart(container), "");
    } catch (err) {
      expect((err as LayerError).code).toBe("LAYER_PANE_NOT_FOUND");
    }
  });
});

describe("DEEPEN 2666 — numeric performance assertion", () => {
  it("resolves pane layers across 1000 repeated lookups (a pan/zoom-driven layer re-resolution loop) within a 200ms budget", () => {
    const container = document.createElement("div");
    container.appendChild(document.createElement("canvas"));
    container.appendChild(document.createElement("canvas"));
    const chart = fakeChart(container);

    const startedAt = performance.now();
    for (let i = 0; i < 1000; i++) {
      getPaneLayers(chart, "candle_pane");
    }
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(200);
  });
});

describe("DEEPEN 2666 — gate red reproduction (naive whole-document canvas query vs. the shipped container-scoped query)", () => {
  /**
   * A naive re-implementation someone might write instead of scoping to
   * chart.getDom(paneId, "main")'s own container: query the whole document
   * for canvases. With more than one pane mounted (a multi-pane chart,
   * CH-14), this silently returns whichever pane's canvases happen to come
   * first in document order -- not necessarily the requested pane's own.
   */
  function naiveWholeDocumentPaneLayers(paneId: string) {
    const canvases = Array.from(document.querySelectorAll("canvas"));
    return {
      paneId,
      main: canvases[0] ?? null,
      overlay: canvases[1] ?? null,
      offscreenCapable: supportsOffscreenCanvas(),
    };
  }

  it("적색: naive whole-document query returns pane A's canvases when asked for pane B; 녹색: getPaneLayers returns pane B's own canvases", () => {
    const paneAContainer = document.createElement("div");
    const paneACanvasMain = document.createElement("canvas");
    const paneACanvasOverlay = document.createElement("canvas");
    paneAContainer.append(paneACanvasMain, paneACanvasOverlay);
    document.body.appendChild(paneAContainer);

    const paneBContainer = document.createElement("div");
    const paneBCanvasMain = document.createElement("canvas");
    const paneBCanvasOverlay = document.createElement("canvas");
    paneBContainer.append(paneBCanvasMain, paneBCanvasOverlay);
    document.body.appendChild(paneBContainer);

    try {
      const naive = naiveWholeDocumentPaneLayers("pane_b");
      expect(naive.main).toBe(paneACanvasMain);
      expect(naive.main).not.toBe(paneBCanvasMain);

      const real = getPaneLayers(fakeChart(paneBContainer), "pane_b");
      expect(real.main).toBe(paneBCanvasMain);
      expect(real.overlay).toBe(paneBCanvasOverlay);
    } finally {
      document.body.removeChild(paneAContainer);
      document.body.removeChild(paneBContainer);
    }
  });
});
