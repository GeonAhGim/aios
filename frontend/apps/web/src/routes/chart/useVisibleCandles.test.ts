import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { ViewportError } from "@aios/chart-engine/src/render/viewport";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { computeVisibleCandles, useVisibleCandles } from "./useVisibleCandles";

const MINUTE_MS = 60_000;

function candle(index: number, overrides: Partial<{ high: number; low: number }> = {}): StreamCandle {
  const openTimeMs = index * MINUTE_MS;
  const high = overrides.high ?? 101;
  const low = overrides.low ?? 99;
  return {
    openTimeMs,
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1m" },
      open_time: String(openTimeMs),
      close_time: String(openTimeMs + MINUTE_MS),
      open: "100",
      high: String(high),
      low: String(low),
      close: "100",
      volume: "1",
      quote_volume: null,
    },
  };
}

const DENSE_COUNT = 100_000;
const SPIKE_INDEX = 42_000;
const DIP_INDEX = 77_000;
const SPIKE_HIGH = 999_999;
const DIP_LOW = -999_999;

function denseSeries(): readonly StreamCandle[] {
  const out: StreamCandle[] = new Array(DENSE_COUNT);
  for (let i = 0; i < DENSE_COUNT; i++) {
    if (i === SPIKE_INDEX) out[i] = candle(i, { high: SPIKE_HIGH });
    else if (i === DIP_INDEX) out[i] = candle(i, { low: DIP_LOW });
    else out[i] = candle(i);
  }
  return out;
}

describe("computeVisibleCandles — CH-19c LOD/viewport wiring", () => {
  it("downsamples a 100k-candle series to the pixel budget while preserving the interval's high/low extremes", () => {
    const candles = denseSeries();
    const viewport = { startTime: candles[0]!.openTimeMs, endTime: candles[candles.length - 1]!.openTimeMs };

    const result = computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 });

    expect(result.length).toBeLessThanOrEqual(2000);
    expect(result.some((c) => c.high === SPIKE_HIGH)).toBe(true);
    expect(result.some((c) => c.low === DIP_LOW)).toBe(true);
  });

  it("culls candles outside the requested viewport before downsampling", () => {
    const candles = [candle(0), candle(1), candle(2), candle(3), candle(4)];
    const viewport = { startTime: candles[1]!.openTimeMs, endTime: candles[2]!.openTimeMs };

    const result = computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 });

    expect(result.map((c) => c.time)).toEqual([candles[1]!.openTimeMs, candles[2]!.openTimeMs]);
  });

  it("rejects an inverted viewport range explicitly instead of silently returning an empty array", () => {
    const candles = [candle(0), candle(1)];
    const viewport = { startTime: candles[1]!.openTimeMs, endTime: candles[0]!.openTimeMs };

    expect(() => computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 })).toThrow(ViewportError);
  });

  it("useVisibleCandles (hook form) throws the same explicit rejection on render, it does not swallow it", () => {
    const candles = [candle(0), candle(1)];
    const viewport = { startTime: candles[1]!.openTimeMs, endTime: candles[0]!.openTimeMs };

    expect(() => renderHook(() => useVisibleCandles(candles, { viewport, targetPixelWidth: 1000 }))).toThrow(
      ViewportError,
    );
  });
});
