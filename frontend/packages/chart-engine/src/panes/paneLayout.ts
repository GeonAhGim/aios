/**
 * CH-14 — turns a `PaneModel`'s height ratios into pixel rects, and gives
 * each pane its own independent `PriceScale` (CH-1b `core/priceScale.ts` —
 * reused as-is, not reinvented: this module only owns per-pane bookkeeping
 * around it). A pane's scale range is never touched by another pane's range
 * change or by a resize of a *different* pane — only that pane's own height
 * changes when the layout is recomputed.
 */

import { type PriceScale, createPriceScale } from "../core/priceScale";
import type { PaneModel, PaneSpec } from "./paneModel";

export interface PaneRect {
  readonly id: string;
  readonly top: number;
  readonly height: number;
}

export type PaneLayoutErrorCode = "PANE_LAYOUT_NOT_FOUND" | "PANE_LAYOUT_HEIGHT_INVALID";

export class PaneLayoutError extends Error {
  readonly code: PaneLayoutErrorCode;

  constructor(code: PaneLayoutErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "PaneLayoutError";
    this.code = code;
  }
}

/**
 * Splits `totalHeight` px across `panes` (top-to-bottom, in array order) by
 * `heightRatio`. Every ratio but the last is floored to a whole pixel; the
 * last pane absorbs the remainder so rects always sum to exactly
 * `totalHeight` regardless of rounding.
 */
export function computePaneRects(panes: readonly PaneSpec[], totalHeight: number): readonly PaneRect[] {
  if (!(totalHeight >= 0)) {
    throw new PaneLayoutError("PANE_LAYOUT_HEIGHT_INVALID", `totalHeight must be >= 0, got ${totalHeight}`);
  }
  const rects: PaneRect[] = [];
  let top = 0;
  let used = 0;
  panes.forEach((pane, index) => {
    const isLast = index === panes.length - 1;
    const height = isLast ? totalHeight - used : Math.floor(pane.heightRatio * totalHeight);
    rects.push({ id: pane.id, top, height });
    top += height;
    used += height;
  });
  return rects;
}

export interface PaneScaleSet {
  /** The `PriceScale` for `paneId`. Throws `PANE_LAYOUT_NOT_FOUND` if `sync` was never called with that pane. */
  scaleFor(paneId: string): PriceScale;
  has(paneId: string): boolean;
  /**
   * Recomputes rects for `model` at `totalHeight`. Panes that still exist
   * keep their existing `PriceScale` instance (and whatever range was set on
   * it) with only its height updated; panes removed from `model` drop their
   * scale; new panes get a fresh scale at the default range.
   */
  sync(model: PaneModel, totalHeight: number): readonly PaneRect[];
}

export function createPaneScaleSet(): PaneScaleSet {
  const scales = new Map<string, PriceScale>();

  function scaleFor(paneId: string): PriceScale {
    const scale = scales.get(paneId);
    if (!scale) throw new PaneLayoutError("PANE_LAYOUT_NOT_FOUND", `no pane scale for id "${paneId}"`);
    return scale;
  }

  function sync(model: PaneModel, totalHeight: number): readonly PaneRect[] {
    const rects = computePaneRects(model.panes, totalHeight);
    const liveIds = new Set(rects.map((rect) => rect.id));
    for (const id of scales.keys()) {
      if (!liveIds.has(id)) scales.delete(id);
    }
    for (const rect of rects) {
      const existing = scales.get(rect.id);
      if (existing) {
        existing.setHeight(rect.height);
      } else {
        scales.set(rect.id, createPriceScale({ height: rect.height }));
      }
    }
    return rects;
  }

  return { scaleFor, has: (paneId) => scales.has(paneId), sync };
}
