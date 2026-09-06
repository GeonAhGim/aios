import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LodError, type LodCandle, downsampleLOD } from "../lod";

function makeCandle(time: number, o: number, h: number, l: number, c: number): LodCandle {
  return { time, open: o, high: h, low: l, close: c, volume: 1 };
}

/** Flat candles, except index `spikeIndex` pokes above/below the rest of its bucket. */
function seriesWithInteriorSpike(count: number, spikeIndex: number, spikeHigh: number, spikeLow: number): LodCandle[] {
  const out: LodCandle[] = [];
  for (let i = 0; i < count; i++) {
    if (i === spikeIndex) out.push(makeCandle(i, 100, spikeHigh, spikeLow, 100));
    else out.push(makeCandle(i, 100, 101, 99, 100));
  }
  return out;
}

describe("downsampleLOD", () => {
  it("preserves the true high/low extreme of a 100,000-candle series downsampled to width 1000", () => {
    const total = 100_000;
    const targetWidth = 1000;
    const bucketSpan = total / targetWidth; // 100 candles per bucket
    // Put the spike on the *interior* candle of bucket 42 (never the bucket's first or last candle),
    // so a naive first/last-only sampler would miss it entirely.
    const bucketStart = 42 * bucketSpan;
    const spikeIndex = bucketStart + Math.floor(bucketSpan / 2);
    const candles = seriesWithInteriorSpike(total, spikeIndex, 999, -999);

    const result = downsampleLOD(candles, targetWidth);

    expect(result).toHaveLength(targetWidth);
    const bucket = result[42]!;
    expect(bucket.high).toBe(999);
    expect(bucket.low).toBe(-999);
  });

  it("every output bucket's high/low exactly matches the max/min of its source range", () => {
    const candles: LodCandle[] = [];
    for (let i = 0; i < 40; i++) {
      // Deterministic pseudo-random-looking highs/lows so extremes aren't at bucket edges.
      const wiggle = ((i * 37) % 11) - 5;
      candles.push(makeCandle(i, 100, 110 + wiggle, 90 - wiggle, 100));
    }
    const targetWidth = 8; // 5 candles per bucket

    const result = downsampleLOD(candles, targetWidth);

    expect(result).toHaveLength(targetWidth);
    for (let b = 0; b < targetWidth; b++) {
      const start = b * 5;
      const end = start + 5;
      const slice = candles.slice(start, end);
      expect(result[b]!.high).toBe(Math.max(...slice.map((c) => c.high)));
      expect(result[b]!.low).toBe(Math.min(...slice.map((c) => c.low)));
      expect(result[b]!.open).toBe(slice[0]!.open);
      expect(result[b]!.close).toBe(slice.at(-1)!.close);
    }
  });

  it("returns the original series unchanged when it already fits within the target width", () => {
    const candles = [makeCandle(1, 1, 2, 0, 1), makeCandle(2, 1, 3, 0, 2), makeCandle(3, 2, 4, 1, 3)];

    expect(downsampleLOD(candles, 1000)).toEqual(candles);
    expect(downsampleLOD(candles, candles.length)).toEqual(candles);
  });

  it("rejects a target pixel width of zero or negative (negative)", () => {
    const candles = [makeCandle(1, 1, 2, 0, 1)];
    expect(() => downsampleLOD(candles, 0)).toThrow(LodError);
    expect(() => downsampleLOD(candles, -5)).toThrow(LodError);
    try {
      downsampleLOD(candles, 0);
    } catch (err) {
      expect((err as LodError).code).toBe("LOD_INVALID_TARGET_WIDTH");
    }
  });

  it("rejects an empty candle array instead of silently returning one (negative)", () => {
    expect(() => downsampleLOD([], 1000)).toThrow(LodError);
    try {
      downsampleLOD([], 1000);
    } catch (err) {
      expect((err as LodError).code).toBe("LOD_EMPTY_CANDLES");
    }
  });

  it("rejects candles whose time runs in reverse (negative)", () => {
    const candles = [makeCandle(2, 1, 2, 0, 1), makeCandle(1, 1, 2, 0, 1)];
    expect(() => downsampleLOD(candles, 10)).toThrow(LodError);
    try {
      downsampleLOD(candles, 10);
    } catch (err) {
      expect((err as LodError).code).toBe("LOD_TIME_NOT_ASCENDING");
    }
  });
});

describe("lod.ts purity (I-10, per DoD: no DOM/canvas/vendor import)", () => {
  it("has zero DOM/canvas/vendor imports — pure computation only", () => {
    const source = readFileSync(fileURLToPath(new URL("../lod.ts", import.meta.url)), "utf8");
    expect(source).not.toMatch(/\bimport\b/);
    expect(source).not.toMatch(/\bdocument\.|\bwindow\.|HTMLCanvasElement|CanvasRenderingContext2D/);
    expect(source).not.toMatch(/from ["']\.\.\/\.\.\/vendor/);
    expect(source.split("\n").length).toBeLessThanOrEqual(300);
  });
});
