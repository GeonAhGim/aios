import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { parseCandleSeries, type CandleRecord, type SeriesKey } from "@aios/shared-types";
import type { CandleQueryResult } from "@aios/api-client";
import { useChartReplaySession, type UseChartReplaySessionOptions } from "./useChartReplaySession";

// candleStream.test.ts와 동일한 관용: LA-24 원본 body를 실제 task-629 파서로
// 통과시켜 ParsedCandleSeries를 만든다(파서 재구현 금지).
const KEY: SeriesKey = { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" };
const H = 3_600_000;
const T0 = Date.parse("2026-09-01T00:00:00Z");

function iso(ms: number): string {
  return new Date(ms).toISOString().replace(".000Z", "Z");
}

function candle(i: number, overrides: Partial<CandleRecord> = {}): CandleRecord {
  return {
    key: KEY,
    open_time: iso(T0 + i * H),
    close_time: iso(T0 + (i + 1) * H),
    open: "100",
    high: "110",
    low: "90",
    close: "105",
    volume: "1",
    quote_volume: null,
    ...overrides,
  };
}

function queryResult(count: number): CandleQueryResult {
  const candles = Array.from({ length: count }, (_, i) => candle(i));
  const series = parseCandleSeries({
    key: KEY,
    candles,
    gaps: [],
    adjustment: "RAW",
    as_of: iso(T0 + count * H),
    series_hash: "h",
    schema_version: "v1",
  });
  return { series, quality: null };
}

const BASE_OPTIONS: UseChartReplaySessionOptions = {
  venue: "BITGET",
  instrumentId: "BTCUSDT",
  timeframe: "1h",
  queryData: undefined,
};

function setup(options: UseChartReplaySessionOptions = BASE_OPTIONS) {
  return renderHook((props: UseChartReplaySessionOptions) => useChartReplaySession(props), { initialProps: options });
}

describe("성공: 데이터 도착 → 스트림 병합", () => {
  it("queryData가 도착하면 같은 스트림에 병합되어 points/replayDisabled에 실측 반영된다", async () => {
    const { result, rerender } = setup();
    await waitFor(() => expect(result.current.replayRef.current).not.toBeNull());
    expect(result.current.replayDisabled).toBe(true);
    expect(result.current.points).toHaveLength(0);

    rerender({ ...BASE_OPTIONS, queryData: queryResult(5) });

    await waitFor(() => expect(result.current.snapshot?.candles.length).toBe(5));
    expect(result.current.replayDisabled).toBe(false);
    expect(result.current.points).toHaveLength(5);
    // toChartPoints(chartPageHelpers.ts)가 실제로 변환한 값 — 수기 좌표가 아니다.
    expect(result.current.points[0]).toEqual({ time: Math.floor(T0 / 1000), open: 100, high: 110, low: 90, close: 105 });
  });
});

describe("negative: instrumentId가 없으면 스트림/리플레이를 만들지 않는다", () => {
  it("replayRef가 null로 남고 points는 빈 배열이며 replayDisabled다", () => {
    const { result } = setup({ ...BASE_OPTIONS, instrumentId: null });

    expect(result.current.replayRef.current).toBeNull();
    expect(result.current.snapshot).toBeNull();
    expect(result.current.points).toEqual([]);
    expect(result.current.replayDisabled).toBe(true);
  });
});

describe("negative: 범위 밖 speed/step은 chart-engine replayController가 거부한다", () => {
  it("setSpeed(0)과 setSpeed(-1)은 RangeError, step(2)는 delta가 1|-1이 아니면 RangeError다", async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.replayRef.current).not.toBeNull());
    const controller = result.current.replayRef.current!;

    expect(() => controller.setSpeed(0)).toThrow(RangeError);
    expect(() => controller.setSpeed(-1)).toThrow(RangeError);
    expect(() => controller.setSpeed(Number.POSITIVE_INFINITY)).toThrow(RangeError);
    // @ts-expect-error step은 1|-1만 허용 — 배선이 실제로 그 계약을 그대로 노출하는지 실측한다.
    expect(() => controller.step(2)).toThrow(RangeError);
  });
});
