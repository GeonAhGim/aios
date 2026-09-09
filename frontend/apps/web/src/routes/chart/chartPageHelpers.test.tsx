import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import {
  IDLE_REPLAY_STATE,
  NoInstrumentSelected,
  REAL_CLOCK,
  TIMEFRAME_MS,
  createDrawing,
  decodeCompareSymbol,
  drawingLabel,
  encodeCompareSymbol,
  toChartPoints,
} from "./chartPageHelpers";

afterEach(() => cleanup());

describe("encodeCompareSymbol/decodeCompareSymbol", () => {
  it("venue:instrumentId 형식으로 인코딩하고 되돌려 디코딩한다", () => {
    const ref = { venue: "BITGET" as const, instrumentId: "BTCUSDT" };
    const encoded = encodeCompareSymbol(ref);

    expect(encoded).toBe("BITGET:BTCUSDT");
    expect(decodeCompareSymbol(encoded)).toEqual(ref);
  });

  it("콜론이 없으면 null을 반환한다", () => {
    expect(decodeCompareSymbol("invalid")).toBeNull();
  });

  it("instrumentId 자체에 콜론이 있어도 첫 콜론까지만 venue로 자른다", () => {
    expect(decodeCompareSymbol("KIS_US:BRK.A:EXTRA")).toEqual({
      venue: "KIS_US",
      instrumentId: "BRK.A:EXTRA",
    });
  });
});

describe("TIMEFRAME_MS", () => {
  it("각 타임프레임을 밀리초로 매핑한다", () => {
    expect(TIMEFRAME_MS["1m"]).toBe(60_000);
    expect(TIMEFRAME_MS["1h"]).toBe(60 * 60_000);
    expect(TIMEFRAME_MS["1d"]).toBe(24 * 60 * 60_000);
  });
});

describe("toChartPoints", () => {
  it("StreamCandle을 CandlestickPoint로 변환하고 openTimeMs를 초 단위로 내림한다", () => {
    const candles: StreamCandle[] = [
      {
        openTimeMs: 1_500,
        confirmed: true,
        record: { open: "100", high: "110", low: "90", close: "105" } as unknown as StreamCandle["record"],
      },
    ];

    expect(toChartPoints(candles)).toEqual([{ time: 1, open: 100, high: 110, low: 90, close: 105 }]);
  });
});

describe("drawingLabel", () => {
  it("종류별로 서로 다른 라벨 문구를 만든다", () => {
    expect(drawingLabel({ id: "d1", kind: "horizontal-line", price: 100 })).toBe("수평선 @100");
    expect(drawingLabel({ id: "d2", kind: "vertical-line", time: 42 })).toBe("수직선 @42");
    expect(
      drawingLabel({ id: "d3", kind: "trendline", points: [{ time: 1, price: 10 }, { time: 2, price: 20 }] }),
    ).toBe("추세선 (1→2)");
  });
});

describe("createDrawing", () => {
  it("kind별로 알맞은 도형을 만든다(horizontal-line/vertical-line은 클릭 시점 값을 그대로 쓴다)", () => {
    expect(createDrawing("h1", "horizontal-line", 10, 123.45)).toEqual({
      id: "h1",
      kind: "horizontal-line",
      price: 123.45,
    });
    expect(createDrawing("v1", "vertical-line", 10, 123.45)).toEqual({ id: "v1", kind: "vertical-line", time: 10 });
  });

  it("trendline은 클릭 시점을 두 번째 앵커로 삼고 첫 앵커는 한 틱 이전이다", () => {
    const d = createDrawing("t1", "trendline", 10, 100);
    expect(d).toEqual({
      id: "t1",
      kind: "trendline",
      points: [
        { time: 9, price: 100 },
        { time: 10, price: 100 },
      ],
    });
  });

  it("rectangle/fibonacci는 클릭 시점 가격을 중심으로 ±1% 폭의 두 앵커를 만든다", () => {
    const d = createDrawing("r1", "rectangle", 10, 100);
    expect(d.kind).toBe("rectangle");
    if (d.kind !== "rectangle") throw new Error("unreachable");
    expect(d.points[0]).toEqual({ time: 9, price: 99 });
    expect(d.points[1]).toEqual({ time: 10, price: 101 });
  });
});

describe("NoInstrumentSelected", () => {
  it("안내 문구와 심볼 목록으로 이동하는 링크를 보여준다", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <NoInstrumentSelected />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/심볼을 먼저 선택하세요/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "심볼 목록으로 이동" })).toHaveAttribute(
      "href",
      "/market/instruments",
    );
  });
});

describe("REAL_CLOCK/IDLE_REPLAY_STATE", () => {
  it("REAL_CLOCK은 window.setTimeout/clearTimeout에 그대로 위임한다", () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
    const cb = () => {};

    const timer = REAL_CLOCK.setTimeout(cb, 10);
    expect(setTimeoutSpy).toHaveBeenCalledWith(cb, 10);

    REAL_CLOCK.clearTimeout(timer);
    expect(clearTimeoutSpy).toHaveBeenCalledWith(timer);

    setTimeoutSpy.mockRestore();
    clearTimeoutSpy.mockRestore();
  });

  it("IDLE_REPLAY_STATE는 정지·빈 상태의 정적 기본값이다", () => {
    expect(IDLE_REPLAY_STATE).toEqual({
      status: "paused",
      speed: 1,
      cursorTs: null,
      visibleCount: 0,
      totalCount: 0,
      atEnd: true,
    });
  });
});
