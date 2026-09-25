import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { LodError, type LodCandle } from "@aios/chart-engine/src/render/lod";
import { ViewportError, cullToViewport } from "@aios/chart-engine/src/render/viewport";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { computeVisibleCandles, useVisibleCandles } from "./useVisibleCandles";

// DEPTH_CH(task-2729) 감사: task-2043(117e023c)의 negative 2건이 전부 동일 축
// (ViewportError, 역전 뷰포트)이었다 — 이 파일은 cullToViewport만 실제 모듈을
// 감싸는 vi.fn으로 두고(mockImplementationOnce로 필요한 테스트에서만 override),
// 나머지 테스트는 실제 구현 그대로 수행되게 한다.
vi.mock("@aios/chart-engine/src/render/viewport", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/chart-engine/src/render/viewport")>();
  return { ...actual, cullToViewport: vi.fn(actual.cullToViewport) };
});

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

  // DEPTH_CH(task-2729) 감사: 위 두 건은 전부 같은 축(ViewportError,
  // VIEWPORT_INVALID_RANGE 역전 뷰포트)이었다 — 아래 두 건은 서로 다른 축
  // (VIEWPORT_TIME_NOT_ASCENDING·LOD_INVALID_TARGET_WIDTH)으로 >=3 축을 채운다.
  it("negative: 캔들이 오름차순이 아니면(실시간 스트림 순서 뒤섞임 등) VIEWPORT_TIME_NOT_ASCENDING으로 명시적으로 거부한다", () => {
    const candles = [candle(0), candle(2), candle(1)];
    const viewport = { startTime: candles[0]!.openTimeMs, endTime: candles[1]!.openTimeMs };

    let thrown: unknown;
    try {
      computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 });
    } catch (caught) {
      thrown = caught;
    }

    expect(thrown).toBeInstanceOf(ViewportError);
    expect((thrown as ViewportError).code).toBe("VIEWPORT_TIME_NOT_ASCENDING");
  });

  it("negative: targetPixelWidth가 0이면(렌더 표면 폭 계산 결함 등) 컬링을 통과한 뒤 다운샘플 단계에서 LOD_INVALID_TARGET_WIDTH로 명시적으로 거부한다", () => {
    const candles = [candle(0), candle(1), candle(2)];
    const viewport = { startTime: candles[0]!.openTimeMs, endTime: candles[2]!.openTimeMs };

    let thrown: unknown;
    try {
      computeVisibleCandles(candles, { viewport, targetPixelWidth: 0 });
    } catch (caught) {
      thrown = caught;
    }

    expect(thrown).toBeInstanceOf(LodError);
    expect((thrown as LodError).code).toBe("LOD_INVALID_TARGET_WIDTH");
  });
});

// DEPTH_CH(task-2729) 감사: task-2043(117e023c)에 mock 실패주입이 없었다.
// cullToViewport(순수 함수 자체는 I/O 없음)를 vi.fn으로 감싸 예기치 못한
// 내부 오류를 강제로 주입하고, computeVisibleCandles가 그것을 빈 배열 등으로
// 무음 삼키지 않고 그대로 전파하는지(fail-closed) 확인한다.
describe("computeVisibleCandles — mock 실패주입(CH-19c 배선 fail-closed)", () => {
  it("cullToViewport가 예기치 못한 오류로 실패해도 삼키지 않고 그대로 전파한다(빈 배열로 무음 폴백하지 않는다)", () => {
    vi.mocked(cullToViewport).mockImplementationOnce(() => {
      throw new Error("SIMULATED_VIEWPORT_FAULT");
    });
    const candles = [candle(0), candle(1), candle(2)];
    const viewport = { startTime: candles[0]!.openTimeMs, endTime: candles[2]!.openTimeMs };

    expect(() => computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 })).toThrow(
      "SIMULATED_VIEWPORT_FAULT",
    );
  });
});

function toLodCandleForRedTest(c: StreamCandle): LodCandle {
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
 * CH-19c 배선 이전(task-2027 unwired-modules-baseline.json이 render/lod·
 * render/viewport를 "landed-but-unconsumed"로 등재했던 상태)을 흉내낸 legacy
 * 목업 — culling/다운샘플 없이 캔들을 그대로 렌더 경로에 넘긴다. 실제
 * production 코드가 아니다.
 */
function naivePassThroughNoLod(candles: readonly StreamCandle[]): readonly LodCandle[] {
  return candles.map(toLodCandleForRedTest);
}

// DEPTH_CH(task-2729) 감사: task-2043(117e023c)에 게이트 적색 재현이 없었다 —
// "배선을 되돌리면 렌더 경로가 10만봉을 그대로 받는다"는 주장이 커밋 메시지
// 뿐이었다. 아래는 그 되돌린 상태(red)와 실 배선(green)을 같은 입력으로 직접
// 대조한다.
describe("computeVisibleCandles — 게이트 적색 재현(CH-19c 배선 없으면 픽셀 예산을 못 지킨다)", () => {
  it("red: 배선 없이(naive pass-through) 10만봉을 그대로 넘기면 픽셀 예산(<=2000)을 크게 초과한다", () => {
    const candles = denseSeries();

    const naiveResult = naivePassThroughNoLod(candles);

    expect(naiveResult.length).toBe(DENSE_COUNT);
    expect(naiveResult.length).toBeGreaterThan(2000);
  });

  it("green: 실 computeVisibleCandles(CH-19c 배선)는 동일 입력을 예산 안으로 낮춘다(legacy와 달리 무음 폴백 아님, 극값도 보존)", () => {
    const candles = denseSeries();
    const viewport = { startTime: candles[0]!.openTimeMs, endTime: candles[candles.length - 1]!.openTimeMs };

    const result = computeVisibleCandles(candles, { viewport, targetPixelWidth: 1000 });

    expect(result.length).toBeLessThanOrEqual(2000);
    expect(result.some((c) => c.high === SPIKE_HIGH)).toBe(true);
    expect(result.some((c) => c.low === DIP_LOW)).toBe(true);
  });
});
