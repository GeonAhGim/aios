/**
 * CH-19b — layer wiring: exposes klinecharts' existing per-pane canvas split
 * (vendor/klinecharts/src/widget/DrawWidget.ts: a `_mainCanvas` for
 * candles/indicators/grid and a separate `_overlayCanvas` for
 * tooltip/crosshair/drawings, stacked in the same container) to the wrapper
 * layer. That split IS the "layer separation" CH-19 asks for — the vendor
 * already keeps rarely-changing content (main) off the canvas that redraws
 * on every mouse move (overlay), so this module locates and reports those
 * two canvases rather than re-deriving the split (ADR-2026-09-06-F D3).
 *
 * This module never draws and never re-implements vendor's render loop:
 * panes are reached only through `chart.getDom()`, the same public DOM
 * seam `Chart.ts` exposes to embedders. Offscreen-canvas eligibility is
 * reported at the environment level, not by calling
 * `transferControlToOffscreen()` on a vendor canvas — `Canvas.ts` calls
 * `getContext('2d')` in its constructor, and the spec forbids transferring
 * control of a canvas once a rendering context has already been requested
 * on it, so attempting that here would throw for every vendor-owned canvas.
 * A caller that owns canvas creation itself (i.e. before handing it to
 * vendor code) can use `supportsOffscreenCanvas()` to decide whether to
 * route that canvas to a worker; this module does not attempt to do so on
 * vendor's behalf.
 */

import type { VendorChart } from "../core/klinecharts";

export type LayerName = "main" | "overlay";

export interface PaneLayers {
  readonly paneId: string;
  /** Vendor's `_mainCanvas` element — candles/indicators/grid, redrawn on data/layout change. */
  readonly main: HTMLCanvasElement | null;
  /** Vendor's `_overlayCanvas` element — crosshair/tooltip/drawings, redrawn on every pointer move. */
  readonly overlay: HTMLCanvasElement | null;
  /** Whether this runtime exposes `OffscreenCanvas` at all (see module docstring for why this is environment-level, not per-canvas). */
  readonly offscreenCapable: boolean;
}

export type LayerErrorCode = "LAYER_PANE_NOT_FOUND";

export class LayerError extends Error {
  readonly code: LayerErrorCode;

  constructor(code: LayerErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "LayerError";
    this.code = code;
  }
}

/** True when the runtime exposes `OffscreenCanvas` (absent in this package's Node/jsdom test env and in some older browsers). */
export function supportsOffscreenCanvas(): boolean {
  return typeof OffscreenCanvas !== "undefined";
}

function findCanvases(container: HTMLElement | null): HTMLCanvasElement[] {
  if (container === null) return [];
  return Array.from(container.querySelectorAll("canvas"));
}

/**
 * Locates one pane's main/overlay canvas pair through the vendor's public
 * `getDom(paneId, "main")` seam, which returns
 * `pane.getMainWidget().getContainer()` — the `<div>` `DrawWidget`'s
 * constructor appends exactly two `<canvas>` elements to, main first, then
 * overlay (see vendor/klinecharts/src/widget/DrawWidget.ts).
 */
export function getPaneLayers(chart: VendorChart, paneId: string): PaneLayers {
  const container = chart.getDom(paneId, "main");
  const canvases = findCanvases(container);
  if (canvases.length === 0) {
    throw new LayerError("LAYER_PANE_NOT_FOUND", `no widget container found for pane "${paneId}"`);
  }
  return {
    paneId,
    main: canvases[0] ?? null,
    overlay: canvases[1] ?? null,
    offscreenCapable: supportsOffscreenCanvas(),
  };
}
