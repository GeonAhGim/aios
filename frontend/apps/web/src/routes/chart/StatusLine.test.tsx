import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CandleRecord, SeriesKey } from "@aios/shared-types";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { StatusLine } from "./StatusLine";

afterEach(() => {
  cleanup();
});

const KEY: SeriesKey = { venue: "BITGET", instrument_id: "BTC-KRW", timeframe: "1m" };

function candle(openTimeMs: number, open: string, high: string, low: string, close: string, volume: string): StreamCandle {
  const record: CandleRecord = {
    key: KEY,
    open_time: new Date(openTimeMs).toISOString(),
    close_time: new Date(openTimeMs + 60_000).toISOString(),
    open,
    high,
    low,
    close,
    volume,
    quote_volume: null,
  };
  return { openTimeMs, record, confirmed: true };
}

// 상승 종가(105 -> 115)라 Chg/Chg%가 양수로 계산된다.
const CANDLE_1 = candle(1_000, "100.00", "110.00", "95.00", "105.00", "12");
const CANDLE_2 = candle(2_000, "105.00", "120.00", "100.00", "115.00", "20");

describe("StatusLine — CH-16e OHLCV 상태줄", () => {
  it("정상 렌더: 크로스헤어가 없으면 마지막 캔들의 OHLCV·등락을 보여준다", () => {
    render(<StatusLine candles={[CANDLE_1, CANDLE_2]} crosshairTimeMs={null} />);

    expect(screen.getByTestId("chart-status-line-O")).toHaveTextContent("105.00");
    expect(screen.getByTestId("chart-status-line-H")).toHaveTextContent("120.00");
    expect(screen.getByTestId("chart-status-line-L")).toHaveTextContent("100.00");
    expect(screen.getByTestId("chart-status-line-C")).toHaveTextContent("115.00");
    expect(screen.getByTestId("chart-status-line-Vol")).toHaveTextContent("20");
    expect(screen.getByTestId("chart-status-line-Chg")).toHaveTextContent("+10.00");
    expect(screen.getByTestId("chart-status-line-Chg%")).toHaveTextContent("+9.52%");
  });

  it("빈 상태: 캔들이 없으면 모든 항목이 기본값(--)으로 표시된다", () => {
    render(<StatusLine candles={[]} crosshairTimeMs={null} />);

    for (const title of ["O", "H", "L", "C", "Vol", "Chg", "Chg%"]) {
      expect(screen.getByTestId(`chart-status-line-${title}`)).toHaveTextContent("--");
    }
  });

  it("경계 입력: 첫 캔들(이전 캔들 없음)에서는 OHLCV는 실제 값, Chg/Chg%만 기본값(--)이다", () => {
    render(<StatusLine candles={[CANDLE_1, CANDLE_2]} crosshairTimeMs={1_000} />);

    expect(screen.getByTestId("chart-status-line-O")).toHaveTextContent("100.00");
    expect(screen.getByTestId("chart-status-line-C")).toHaveTextContent("105.00");
    expect(screen.getByTestId("chart-status-line-Vol")).toHaveTextContent("12");
    expect(screen.getByTestId("chart-status-line-Chg")).toHaveTextContent("--");
    expect(screen.getByTestId("chart-status-line-Chg%")).toHaveTextContent("--");
  });
});
