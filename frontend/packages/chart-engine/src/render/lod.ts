/**
 * CH-19a (1/2) — level-of-detail downsampling for the candle series before it
 * reaches a renderer. Pure value-space math: no DOM/canvas/vendor dependency,
 * so it can run off the main thread or in a plain unit test.
 *
 * A naive "keep the bucket's first and last candle" downsampler is *not*
 * acceptable here: whenever a spike's extreme sits on an interior candle of a
 * bucket, that implementation loses it, which is exactly the failure this
 * module exists to avoid (a wick that touched a key level must still show up
 * at any zoom level). Every bucket's `high`/`low` is therefore the true
 * max/min over every candle the bucket covers, not just its endpoints.
 */

export interface LodCandle {
  readonly time: number;
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
  readonly volume?: number;
}

export type LodErrorCode = "LOD_INVALID_TARGET_WIDTH" | "LOD_EMPTY_CANDLES" | "LOD_TIME_NOT_ASCENDING";

export class LodError extends Error {
  readonly code: LodErrorCode;

  constructor(code: LodErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "LodError";
    this.code = code;
  }
}

function assertAscending(candles: readonly LodCandle[]): void {
  for (let i = 1; i < candles.length; i++) {
    if (candles[i]!.time <= candles[i - 1]!.time) {
      throw new LodError("LOD_TIME_NOT_ASCENDING", `time not strictly ascending at index ${i}`);
    }
  }
}

/** [start, end) index range of the candles that fold into bucket `i` of `bucketCount`. */
function bucketRange(i: number, bucketCount: number, total: number): { start: number; end: number } {
  const start = Math.floor((i * total) / bucketCount);
  const end = i === bucketCount - 1 ? total : Math.floor(((i + 1) * total) / bucketCount);
  return { start, end };
}

function mergeBucket(candles: readonly LodCandle[], start: number, end: number): LodCandle {
  let high = candles[start]!.high;
  let low = candles[start]!.low;
  let volume: number | undefined = candles[start]!.volume;
  for (let i = start + 1; i < end; i++) {
    const c = candles[i]!;
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
    if (c.volume !== undefined) volume = (volume ?? 0) + c.volume;
  }
  return {
    time: candles[start]!.time,
    open: candles[start]!.open,
    high,
    low,
    close: candles[end - 1]!.close,
    volume,
  };
}

/**
 * Downsamples `candles` to at most `targetPixelWidth` points, preserving each
 * bucket's true high/low extremes exactly. When the series already fits
 * within the target width, it is returned unchanged (one candle per bucket).
 */
export function downsampleLOD(candles: readonly LodCandle[], targetPixelWidth: number): LodCandle[] {
  if (candles.length === 0) throw new LodError("LOD_EMPTY_CANDLES", "candles must not be empty");
  if (!Number.isFinite(targetPixelWidth) || targetPixelWidth < 1) {
    throw new LodError("LOD_INVALID_TARGET_WIDTH", `targetPixelWidth must be a finite number >= 1, got ${targetPixelWidth}`);
  }
  assertAscending(candles);

  const bucketCount = Math.min(candles.length, Math.floor(targetPixelWidth));
  if (bucketCount >= candles.length) return candles.slice();

  const out: LodCandle[] = new Array(bucketCount);
  for (let i = 0; i < bucketCount; i++) {
    const { start, end } = bucketRange(i, bucketCount, candles.length);
    out[i] = mergeBucket(candles, start, end);
  }
  return out;
}
