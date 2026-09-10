import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { LodCandle } from "../lod";
import { ViewportError, cullToViewport } from "../viewport";
import { createRng } from "./arbitraries";

function makeCandle(time: number): LodCandle {
  return { time, open: time, high: time + 1, low: time - 1, close: time, volume: 1 };
}

const SERIES: LodCandle[] = Array.from({ length: 20 }, (_, i) => makeCandle(i * 10)); // times 0,10,...,190

describe("cullToViewport", () => {
  it("excludes out-of-viewport indices — returned range covers only the overlapping candles", () => {
    const result = cullToViewport(SERIES, { startTime: 55, endTime: 95 });

    // times 60,70,80,90 are the only ones inside [55, 95]
    expect(result.startIndex).toBe(6);
    expect(result.endIndex).toBe(10);
    expect(result.candles.map((c) => c.time)).toEqual([60, 70, 80, 90]);
    // every candle outside the returned index range must fall outside the viewport
    for (let i = 0; i < result.startIndex; i++) expect(SERIES[i]!.time).toBeLessThan(55);
    for (let i = result.endIndex; i < SERIES.length; i++) expect(SERIES[i]!.time).toBeGreaterThan(95);
  });

  it("returns the original series unchanged when the viewport covers the whole range", () => {
    const result = cullToViewport(SERIES, { startTime: SERIES[0]!.time, endTime: SERIES.at(-1)!.time });

    expect(result.startIndex).toBe(0);
    expect(result.endIndex).toBe(SERIES.length);
    expect(result.candles).toHaveLength(SERIES.length);
    expect(result.candles).toEqual(SERIES);
  });

  it("returns an empty slice (not an error) when the viewport misses the series entirely", () => {
    const result = cullToViewport(SERIES, { startTime: 1000, endTime: 2000 });
    expect(result.candles).toEqual([]);
    expect(result.startIndex).toBe(result.endIndex);
  });

  it("rejects an empty candle array instead of silently returning one (negative)", () => {
    expect(() => cullToViewport([], { startTime: 0, endTime: 1 })).toThrow(ViewportError);
    try {
      cullToViewport([], { startTime: 0, endTime: 1 });
    } catch (err) {
      expect((err as ViewportError).code).toBe("VIEWPORT_EMPTY_CANDLES");
    }
  });

  it("rejects candles whose time runs in reverse (negative)", () => {
    const reversed = [makeCandle(10), makeCandle(0)];
    expect(() => cullToViewport(reversed, { startTime: 0, endTime: 10 })).toThrow(ViewportError);
    try {
      cullToViewport(reversed, { startTime: 0, endTime: 10 });
    } catch (err) {
      expect((err as ViewportError).code).toBe("VIEWPORT_TIME_NOT_ASCENDING");
    }
  });

  it("rejects a viewport whose start comes after its end (negative)", () => {
    expect(() => cullToViewport(SERIES, { startTime: 100, endTime: 0 })).toThrow(ViewportError);
    try {
      cullToViewport(SERIES, { startTime: 100, endTime: 0 });
    } catch (err) {
      expect((err as ViewportError).code).toBe("VIEWPORT_INVALID_RANGE");
    }
  });
});

// --- DEEPEN 1958 (docs/audit/DEPTH_CH.md): the original leaf had no failure
// injection, no ms/fps numeric performance assertion, and no gate-red
// reproduction. lod.ts/viewport.ts are untouched; the three axes below are
// test-only additions.

describe("DEEPEN 1958 — malformed-value failure injection (mocked network/DB injection is structurally impossible for a pure function; this shows the module is immune to it because it only ever reads `.time`)", () => {
  it("corrupting open/high/low/close/volume with NaN/Infinity/negative values never perturbs the culled result (200 seeded corruptions)", () => {
    const range = { startTime: 55, endTime: 95 };
    const clean = cullToViewport(SERIES, range);

    for (let seed = 0; seed < 200; seed++) {
      const rng = createRng(seed);
      const corrupted = SERIES.map((c) => {
        if (!rng.bool()) return c;
        const field = rng.pick(["open", "high", "low", "close", "volume"] as const);
        const value = rng.pick([Number.NaN, Infinity, -Infinity, -1]);
        return { ...c, [field]: value };
      });

      const dirty = cullToViewport(corrupted, range);
      expect(dirty.startIndex).toBe(clean.startIndex);
      expect(dirty.endIndex).toBe(clean.endIndex);
      expect(dirty.candles.map((c) => c.time)).toEqual(clean.candles.map((c) => c.time));
    }
  });
});

describe("DEEPEN 1958 — malformed timestamp bypasses the ascending guard", () => {
  it("a NaN timestamp is not caught by assertAscending (NaN <= x is false in both directions) and can silently exclude later in-range candles from the cull, without throwing", () => {
    // times: 0, 10, NaN, 5, 20 — the drop from 10 to 5 right after the NaN is a
    // real regression, but every comparison touching the NaN evaluates false,
    // so assertAscending's pairwise `<=` check never fires on it.
    const malformed: LodCandle[] = [0, 10, Number.NaN, 5, 20].map((t) => makeCandle(t));

    expect(() => cullToViewport(malformed, { startTime: 0, endTime: 20 })).not.toThrow();
    const result = cullToViewport(malformed, { startTime: 0, endTime: 20 });
    // time=20 is inside [0, 20], but the NaN at index 2 breaks the binary
    // search's monotonicity assumption, so it — and every candle after it,
    // including the in-range time=20 — is silently dropped from the result.
    expect(result.candles.map((c) => c.time)).toEqual([0, 10]);
    expect(result.endIndex).toBe(2);
  });
});

describe("DEEPEN 1958 — numeric performance assertion", () => {
  it("culls a 100,000-candle series across 100 viewport queries (a pan/zoom scrub) within a 1500ms budget", () => {
    // cullToViewport re-validates ascending order on the full array on every
    // call, so its cost is O(candles) per query, not just the O(log candles)
    // binary search -- repeated scrubbing is the realistic worst case.
    const total = 100_000;
    const big: LodCandle[] = Array.from({ length: total }, (_, i) => makeCandle(i));

    const startedAt = performance.now();
    let touched = 0;
    for (let q = 0; q < 100; q++) {
      const t = (q * 37) % total;
      const result = cullToViewport(big, { startTime: t, endTime: t + 50 });
      touched += result.candles.length;
    }
    const elapsedMs = performance.now() - startedAt;

    expect(touched).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(1500);
  });
});

describe("DEEPEN 1958 — gate red reproduction (naive exclusive-end binary search vs. the shipped inclusive cullToViewport)", () => {
  /**
   * A naive viewport cull that treats `endTime` as EXCLUSIVE (a common
   * off-by-one a from-scratch reimplementation would make), unlike the
   * shipped inclusive `[startTime, endTime]` contract.
   */
  function naiveExclusiveEndCull(candles: readonly LodCandle[], viewport: { startTime: number; endTime: number }) {
    let startIndex = 0;
    while (startIndex < candles.length && candles[startIndex]!.time < viewport.startTime) startIndex++;
    let endIndex = startIndex;
    while (endIndex < candles.length && candles[endIndex]!.time < viewport.endTime) endIndex++;
    return { startIndex, endIndex, candles: candles.slice(startIndex, endIndex) };
  }

  it("적색: naive exclusive-end cull drops the candle sitting exactly on endTime; 녹색: cullToViewport keeps it", () => {
    const viewport = { startTime: SERIES[0]!.time, endTime: SERIES.at(-1)!.time };

    const naive = naiveExclusiveEndCull(SERIES, viewport);
    expect(naive.candles).not.toContainEqual(SERIES.at(-1));
    expect(naive.candles).toHaveLength(SERIES.length - 1);

    const real = cullToViewport(SERIES, viewport);
    expect(real.candles).toContainEqual(SERIES.at(-1));
    expect(real.candles).toHaveLength(SERIES.length);
  });
});

describe("viewport.ts purity (I-10, per DoD: no DOM/canvas/vendor import)", () => {
  it("only imports from its sibling lod.ts — no DOM/canvas/vendor dependency", () => {
    const source = readFileSync(fileURLToPath(new URL("../viewport.ts", import.meta.url)), "utf8");
    const importLines = source.match(/^import .*$/gm) ?? [];
    expect(importLines.every((line) => /from "\.\/lod"/.test(line))).toBe(true);
    expect(source).not.toMatch(/\bdocument\.|\bwindow\.|HTMLCanvasElement|CanvasRenderingContext2D/);
    expect(source).not.toMatch(/from ["']\.\.\/\.\.\/vendor/);
    expect(source.split("\n").length).toBeLessThanOrEqual(300);
  });
});
