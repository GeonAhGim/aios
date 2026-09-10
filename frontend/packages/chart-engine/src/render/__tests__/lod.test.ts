import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LodError, type LodCandle, downsampleLOD } from "../lod";
import { createRng } from "./arbitraries";

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

// --- DEEPEN 1958 (docs/audit/DEPTH_CH.md): the original leaf had no failure
// injection, no ms/fps numeric performance assertion, and no gate-red
// reproduction. lod.ts/viewport.ts are untouched; the three axes below are
// test-only additions.

describe("DEEPEN 1958 — malformed-value failure injection (mocked network/DB injection is structurally impossible for a pure function; this fuzzes the corrupted-candle domain a real market-data feed could hand it)", () => {
  function jitteredCandles(rng: ReturnType<typeof createRng>, total: number): LodCandle[] {
    const out: LodCandle[] = [];
    for (let i = 0; i < total; i++) {
      const wiggle = (rng.next() - 0.5) * 20;
      out.push(makeCandle(i, 100, 105 + wiggle, 95 - wiggle, 100));
    }
    return out;
  }

  it("a NaN high/low landing on a bucket's FIRST candle poisons only that bucket's extreme — fail-open, never an uncaught throw (200 seeded corruptions)", () => {
    const total = 300;
    const targetWidth = 30; // 10 candles per bucket
    const bucketSpan = total / targetWidth;
    for (let seed = 0; seed < 200; seed++) {
      const rng = createRng(seed);
      const candles = jitteredCandles(rng, total);
      const corruptBucket = rng.int(0, targetWidth - 1);
      const start = corruptBucket * bucketSpan;
      const field: "high" | "low" = rng.bool() ? "high" : "low";
      const corrupted = candles.slice();
      corrupted[start] = { ...corrupted[start]!, [field]: Number.NaN };

      expect(() => downsampleLOD(corrupted, targetWidth)).not.toThrow();
      const result = downsampleLOD(corrupted, targetWidth);
      expect(Number.isNaN(result[corruptBucket]![field])).toBe(true);

      for (let b = 0; b < targetWidth; b++) {
        if (b === corruptBucket) continue;
        expect(Number.isFinite(result[b]!.high)).toBe(true);
        expect(Number.isFinite(result[b]!.low)).toBe(true);
      }
    }
  });

  it("the same NaN corruption landing on a NON-first candle inside the bucket is silently absorbed — output equals the corruption-free baseline (200 seeded corruptions)", () => {
    const total = 300;
    const targetWidth = 30;
    const bucketSpan = total / targetWidth;
    // Flat (constant) OHLC across every candle: unlike a jittered series, no
    // single candle uniquely carries the bucket's max/min, so knocking out any
    // one NON-first candidate with NaN can never change the winning value —
    // isolating the "interior corruption is a no-op" property from the
    // separate "did we happen to corrupt the actual extreme" concern.
    const candles: LodCandle[] = [];
    for (let i = 0; i < total; i++) candles.push(makeCandle(i, 100, 105, 95, 100));
    const baseline = downsampleLOD(candles, targetWidth);

    for (let seed = 0; seed < 200; seed++) {
      const rng = createRng(seed + 10_000);
      const corruptBucket = rng.int(0, targetWidth - 1);
      const start = corruptBucket * bucketSpan;
      const offset = 1 + rng.int(0, bucketSpan - 2); // strictly interior, never the bucket's first candle
      const field: "high" | "low" = rng.bool() ? "high" : "low";
      const corrupted = candles.slice();
      corrupted[start + offset] = { ...corrupted[start + offset]!, [field]: Number.NaN };

      expect(() => downsampleLOD(corrupted, targetWidth)).not.toThrow();
      const result = downsampleLOD(corrupted, targetWidth);
      expect(result[corruptBucket]).toEqual(baseline[corruptBucket]);
    }
  });

  it("a NaN volume ANYWHERE in the bucket (not just the first candle) poisons the bucket's total — addition-based propagation, unlike high/low's comparison-based containment (200 seeded corruptions)", () => {
    const total = 300;
    const targetWidth = 30;
    const bucketSpan = total / targetWidth;
    for (let seed = 0; seed < 200; seed++) {
      const rng = createRng(seed + 20_000);
      const candles: LodCandle[] = [];
      for (let i = 0; i < total; i++) candles.push(makeCandle(i, 100, 105, 95, 100));

      const corruptBucket = rng.int(0, targetWidth - 1);
      const start = corruptBucket * bucketSpan;
      const offset = rng.int(0, bucketSpan - 1); // any position, including the first candle
      const corrupted = candles.slice();
      corrupted[start + offset] = { ...corrupted[start + offset]!, volume: Number.NaN };

      expect(() => downsampleLOD(corrupted, targetWidth)).not.toThrow();
      const result = downsampleLOD(corrupted, targetWidth);
      expect(Number.isNaN(result[corruptBucket]!.volume)).toBe(true);
    }
  });
});

describe("DEEPEN 1958 — malformed timestamp bypasses the ascending guard", () => {
  it("a NaN timestamp is not caught by assertAscending (NaN <= x is false in both directions) and propagates into a bucket's output time, without throwing", () => {
    // times: 0, 10, NaN, 5, 20 — the drop from 10 to 5 right after the NaN is a
    // real regression, but every comparison touching the NaN evaluates false,
    // so assertAscending's pairwise `<=` check never fires on it.
    const malformed: LodCandle[] = [0, 10, Number.NaN, 5, 20].map((t) => makeCandle(t, 100, 101, 99, 100));

    expect(() => downsampleLOD(malformed, 2)).not.toThrow();
    const result = downsampleLOD(malformed, 2);
    expect(result).toHaveLength(2);
    // bucket 1 starts at index 2 (the corrupted candle), so its time is NaN.
    expect(Number.isNaN(result[1]!.time)).toBe(true);
  });
});

describe("DEEPEN 1958 — numeric performance assertion", () => {
  it("downsamples a 100,000-candle series to width 1000 within a 300ms budget", () => {
    const total = 100_000;
    const candles: LodCandle[] = [];
    for (let i = 0; i < total; i++) {
      const wave = Math.sin(i / 50) * 10;
      candles.push(makeCandle(i, 100, 100 + wave, 100 - wave, 100));
    }

    const startedAt = performance.now();
    const result = downsampleLOD(candles, 1000);
    const elapsedMs = performance.now() - startedAt;

    expect(result).toHaveLength(1000);
    expect(elapsedMs).toBeLessThan(300);
  });
});

describe("DEEPEN 1958 — gate red reproduction (naive first/last-per-bucket downsampler vs. the shipped true-extreme downsampler)", () => {
  /**
   * The naive downsampler this module's own docstring warns against: only
   * the bucket's first and last candle are consulted, so an interior spike
   * (like the one `seriesWithInteriorSpike` plants) is invisible to it.
   */
  function naiveFirstLastDownsample(candles: readonly LodCandle[], targetPixelWidth: number): LodCandle[] {
    const bucketCount = Math.min(candles.length, Math.floor(targetPixelWidth));
    const out: LodCandle[] = new Array(bucketCount);
    for (let i = 0; i < bucketCount; i++) {
      const start = Math.floor((i * candles.length) / bucketCount);
      const end = i === bucketCount - 1 ? candles.length : Math.floor(((i + 1) * candles.length) / bucketCount);
      const first = candles[start]!;
      const last = candles[end - 1]!;
      out[i] = {
        time: first.time,
        open: first.open,
        high: Math.max(first.high, last.high),
        low: Math.min(first.low, last.low),
        close: last.close,
      };
    }
    return out;
  }

  it("적색: naive first/last downsampler misses an interior bucket spike; 녹색: downsampleLOD preserves it", () => {
    const total = 100_000;
    const targetWidth = 1000;
    const bucketSpan = total / targetWidth;
    const bucketStart = 42 * bucketSpan;
    const spikeIndex = bucketStart + Math.floor(bucketSpan / 2);
    const candles = seriesWithInteriorSpike(total, spikeIndex, 999, -999);

    const naive = naiveFirstLastDownsample(candles, targetWidth);
    expect(naive[42]!.high).toBeLessThan(999);
    expect(naive[42]!.low).toBeGreaterThan(-999);

    const real = downsampleLOD(candles, targetWidth);
    expect(real[42]!.high).toBe(999);
    expect(real[42]!.low).toBe(-999);
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
