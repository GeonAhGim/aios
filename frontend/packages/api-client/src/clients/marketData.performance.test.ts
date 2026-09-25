import { afterEach, describe, expect, it, vi } from "vitest";
import { baseParams, candlePage, candleRecord, candleSeriesView, envelope, makeClient, stubFetch } from "./marketData.fixtures";

describe("수치 성능 단언 — 대용량 캔들 시리즈", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("candles 2000개짜리 응답도 짧은 시간 안에 항목별로 판별해 돌려준다(O(n) 유지 확인 — 회귀로 O(n^2)가 되면 이 임계값이 신호를 준다)", async () => {
    const CANDLE_COUNT = 2000;
    const candles = Array.from({ length: CANDLE_COUNT }, (_, i) => ({
      ...candleRecord,
      open_time: new Date(Date.UTC(2026, 8, 1, 0, i)).toISOString(),
      close_time: new Date(Date.UTC(2026, 8, 1, 0, i + 1)).toISOString(),
    }));
    stubFetch(envelope({ ...candleSeriesView, candles }, candlePage));

    const startedAt = performance.now();
    const result = await makeClient().getCandles(baseParams);
    const elapsedMs = performance.now() - startedAt;

    expect(result.series.kind).toBe("ok");
    if (result.series.kind === "ok") {
      expect(result.series.value.candles).toHaveLength(CANDLE_COUNT);
    }
    expect(elapsedMs).toBeLessThan(1000);
  });
});
