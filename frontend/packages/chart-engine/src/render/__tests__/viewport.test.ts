import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { LodCandle } from "../lod";
import { ViewportError, cullToViewport } from "../viewport";

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
