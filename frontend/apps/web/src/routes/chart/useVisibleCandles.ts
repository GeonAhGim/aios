// CH-19c — wires the CH-19a density-reduction pair (`render/lod.ts`,
// `render/viewport.ts`) into the chart screen. Both modules already existed
// (task-2027/2043 baseline: `frontend/scripts/unwired-modules-baseline.json`
// listed `render/lod`/`render/viewport` as landed-but-unconsumed) — this file
// only calls them, it does not re-implement bucket-merge or binary-search
// culling (§C: no duplicate context).
//
// Order matters: cull to the visible time range first, then downsample what's
// left to the pixel budget. Culling before downsampling keeps the LOD bucket
// boundaries aligned to what is actually on screen instead of wasting buckets
// on off-screen candles.
import { cloneElement, isValidElement, useMemo, type ReactElement, type ReactNode } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { downsampleLOD, type LodCandle } from "@aios/chart-engine/src/render/lod";
import { cullToViewport, type ViewportRange } from "@aios/chart-engine/src/render/viewport";
import { CandlestickChart, type CandlestickPoint } from "@aios/ui-web";

export interface VisibleCandlesOptions {
  /** Visible time range (inclusive), same units as `StreamCandle.openTimeMs`. */
  readonly viewport: ViewportRange;
  /** Render surface width in CSS pixels — the LOD bucket budget. */
  readonly targetPixelWidth: number;
}

function toLodCandle(c: StreamCandle): LodCandle {
  return {
    time: c.openTimeMs,
    open: Number(c.record.open),
    high: Number(c.record.high),
    low: Number(c.record.low),
    close: Number(c.record.close),
    volume: c.record.volume === null ? undefined : Number(c.record.volume),
  };
}

/**
 * Pure core of `useVisibleCandles`, exported separately so tests can exercise
 * it without mounting a component. An inverted viewport (`startTime >
 * endTime`) is rejected by `cullToViewport` itself (`ViewportError`) — this
 * function does not catch it, because a silently-returned empty array would
 * be indistinguishable from "no candles in range", a legitimate outcome this
 * function *does* return as `[]` when the viewport simply has no overlap.
 */
export function computeVisibleCandles(
  candles: readonly StreamCandle[],
  options: VisibleCandlesOptions,
): readonly LodCandle[] {
  if (candles.length === 0) return [];
  const lodCandles = candles.map(toLodCandle);
  const culled = cullToViewport(lodCandles, options.viewport);
  if (culled.candles.length === 0) return [];
  return downsampleLOD(culled.candles, options.targetPixelWidth);
}

/** React-memoized wrapper around {@link computeVisibleCandles} for use in render. */
export function useVisibleCandles(
  candles: readonly StreamCandle[],
  options: VisibleCandlesOptions,
): readonly LodCandle[] {
  const { viewport, targetPixelWidth } = options;
  return useMemo(
    () => computeVisibleCandles(candles, { viewport, targetPixelWidth }),
    [candles, viewport, targetPixelWidth],
  );
}

function toCandlestickPoint(c: LodCandle): CandlestickPoint {
  return { time: Math.floor(c.time / 1000), open: c.open, high: c.high, low: c.low, close: c.close };
}

/**
 * ChartPanes.tsx's `children` docstring documents that the only real candle
 * renderer this screen ever mounts is `CandlestickChart` — there is no second
 * render stack to route density reduction through (CH-14/16 decision: no new
 * render stack). When the child element is that renderer, its `data` is
 * swapped for the LOD/viewport-culled series; any other child (e.g. a test
 * stub) passes through unchanged.
 */
function wireCandleRenderer(children: ReactNode, visibleCandles: readonly LodCandle[]): ReactNode {
  if (!isValidElement(children) || children.type !== CandlestickChart) return children;
  return cloneElement(children as ReactElement<{ data: CandlestickPoint[] }>, {
    data: visibleCandles.map(toCandlestickPoint),
  });
}

/** Combines {@link useVisibleCandles} with {@link wireCandleRenderer} — the one call ChartPanes.tsx needs. */
export function useWiredCandleRenderer(
  children: ReactNode,
  candles: readonly StreamCandle[],
  options: VisibleCandlesOptions,
): ReactNode {
  const visibleCandles = useVisibleCandles(candles, options);
  return useMemo(() => wireCandleRenderer(children, visibleCandles), [children, visibleCandles]);
}
