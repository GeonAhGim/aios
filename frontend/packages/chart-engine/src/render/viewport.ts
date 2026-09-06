/**
 * CH-19a (2/2) — viewport culling: narrows a candle series down to the
 * contiguous slice that overlaps a visible time range, so a renderer never
 * has to walk candles that sit off-screen. Pure value-space math (binary
 * search on the time axis), no DOM/canvas/vendor dependency.
 */

import type { LodCandle } from "./lod";

export interface ViewportRange {
  /** Inclusive lower bound of the visible time range. */
  readonly startTime: number;
  /** Inclusive upper bound of the visible time range. */
  readonly endTime: number;
}

export interface ViewportCullResult {
  /** Inclusive start index into the original `candles` array. */
  readonly startIndex: number;
  /** Exclusive end index into the original `candles` array. */
  readonly endIndex: number;
  /** `candles.slice(startIndex, endIndex)` — every candle inside `viewport`. */
  readonly candles: readonly LodCandle[];
}

export type ViewportErrorCode = "VIEWPORT_EMPTY_CANDLES" | "VIEWPORT_TIME_NOT_ASCENDING" | "VIEWPORT_INVALID_RANGE";

export class ViewportError extends Error {
  readonly code: ViewportErrorCode;

  constructor(code: ViewportErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ViewportError";
    this.code = code;
  }
}

function assertAscending(candles: readonly LodCandle[]): void {
  for (let i = 1; i < candles.length; i++) {
    if (candles[i]!.time <= candles[i - 1]!.time) {
      throw new ViewportError("VIEWPORT_TIME_NOT_ASCENDING", `time not strictly ascending at index ${i}`);
    }
  }
}

/** First index whose `time >= t` (candles must already be ascending). */
function lowerBound(candles: readonly LodCandle[], t: number): number {
  let lo = 0;
  let hi = candles.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (candles[mid]!.time < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** First index whose `time > t` (candles must already be ascending). */
function upperBound(candles: readonly LodCandle[], t: number): number {
  let lo = 0;
  let hi = candles.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (candles[mid]!.time <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Returns the contiguous index range of `candles` whose `time` falls inside
 * `[viewport.startTime, viewport.endTime]`, plus the matching slice. A
 * viewport that covers the whole series returns the full array, unchanged.
 */
export function cullToViewport(candles: readonly LodCandle[], viewport: ViewportRange): ViewportCullResult {
  if (candles.length === 0) throw new ViewportError("VIEWPORT_EMPTY_CANDLES", "candles must not be empty");
  assertAscending(candles);
  if (viewport.startTime > viewport.endTime) {
    throw new ViewportError(
      "VIEWPORT_INVALID_RANGE",
      `startTime ${viewport.startTime} must not be greater than endTime ${viewport.endTime}`,
    );
  }

  const startIndex = lowerBound(candles, viewport.startTime);
  const endIndex = upperBound(candles, viewport.endTime);
  return { startIndex, endIndex, candles: candles.slice(startIndex, endIndex) };
}
