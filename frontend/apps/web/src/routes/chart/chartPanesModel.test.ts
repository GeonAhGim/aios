import { describe, expect, it } from "vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { PaneModelError } from "@aios/chart-engine/src/panes/paneModel";
import {
  DEFAULT_TOTAL_HEIGHT,
  EMPTY_OVERLAY_PLOT_SPECS,
  EMPTY_OVERLAY_SERIES,
  EMPTY_STRING_ARRAY,
  MAIN_PANE_ID,
  PANE_ERROR_REASONS,
  SURFACE_WIDTH_PX,
  buildInitialModel,
  overlayIdFromSubPaneId,
  resolveTimeMsFromClientX,
  subPaneId,
} from "./chartPanesModel";

// CH-14 순수 이동(task-2011): ChartPanes.tsx의 페인 id 코덱, 에러 문구,
// 크로스헤어 시간 환산, 초기 PaneModel 조립. 화면 배선(ChartPanes.tsx)은
// ChartPanes.test.tsx가 이미 커버하므로, 여기서는 이 순수 조각 자체의 계약만 확인한다.

function candle(hourOffset: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: "50000.00",
      high: "50500.00",
      low: "49800.00",
      close: "50200.00",
      volume: "12.5",
      quote_volume: "628500.00",
    },
  };
}

describe("chartPanesModel — 페인 id 코덱(subPaneId/overlayIdFromSubPaneId)", () => {
  it("subPaneId는 sub- 접두어를 붙이고, overlayIdFromSubPaneId는 역변환한다", () => {
    expect(subPaneId("RSI")).toBe("sub-RSI");
    expect(overlayIdFromSubPaneId("sub-RSI")).toBe("RSI");
  });

  it("왕복 변환은 원래 지표 id로 돌아온다", () => {
    for (const id of ["SMA", "MACD", "STOCH"]) {
      expect(overlayIdFromSubPaneId(subPaneId(id))).toBe(id);
    }
  });
});

describe("chartPanesModel — buildInitialModel", () => {
  it("서브 오버레이가 없으면 메인 페인 하나뿐인 모델을 만든다", () => {
    const model = buildInitialModel([]);
    expect(model.panes).toEqual([{ id: MAIN_PANE_ID, kind: "main", heightRatio: 1 }]);
  });

  it("서브 오버레이 id마다 sub- 접두 페인을 추가하고, 높이 비율 합은 1이다", () => {
    const model = buildInitialModel(["RSI", "MACD"]);

    expect(model.panes.map((p) => p.id)).toEqual([MAIN_PANE_ID, "sub-RSI", "sub-MACD"]);
    expect(model.panes.map((p) => p.kind)).toEqual(["main", "sub", "sub"]);
    const sum = model.panes.reduce((total, p) => total + p.heightRatio, 0);
    expect(sum).toBeCloseTo(1, 9);
  });

  it("중복된 서브 오버레이 id는 paneModel의 PANE_DUPLICATE_ID로 실패한다(무음 무시 금지)", () => {
    expect(() => buildInitialModel(["RSI", "RSI"])).toThrow(PaneModelError);
  });
});

describe("chartPanesModel — resolveTimeMsFromClientX", () => {
  const candles = [candle(0), candle(1), candle(2), candle(3)];

  it("캔들이 없으면 현재 시각을 반환한다", () => {
    const before = Date.now();
    const result = resolveTimeMsFromClientX(100, []);
    const after = Date.now();
    expect(result).toBeGreaterThanOrEqual(before);
    expect(result).toBeLessThanOrEqual(after);
  });

  it("clientX가 0이면 첫 캔들의 openTimeMs를 반환한다", () => {
    expect(resolveTimeMsFromClientX(0, candles)).toBe(candles[0]!.openTimeMs);
  });

  it("clientX가 SURFACE_WIDTH_PX 이상이면 마지막 캔들의 openTimeMs로 클램프된다", () => {
    expect(resolveTimeMsFromClientX(SURFACE_WIDTH_PX, candles)).toBe(candles[candles.length - 1]!.openTimeMs);
    expect(resolveTimeMsFromClientX(SURFACE_WIDTH_PX * 5, candles)).toBe(candles[candles.length - 1]!.openTimeMs);
  });

  it("음수 clientX는 0으로 클램프되어 첫 캔들 시각을 반환한다", () => {
    expect(resolveTimeMsFromClientX(-50, candles)).toBe(candles[0]!.openTimeMs);
  });

  it("중간 clientX는 첫/마지막 openTimeMs 사이를 선형 보간한다", () => {
    const half = resolveTimeMsFromClientX(SURFACE_WIDTH_PX / 2, candles);
    const firstMs = candles[0]!.openTimeMs;
    const lastMs = candles[candles.length - 1]!.openTimeMs;
    expect(half).toBeCloseTo(firstMs + (lastMs - firstMs) / 2, 6);
  });
});

describe("chartPanesModel — 상수/에러 문구", () => {
  it("PANE_ERROR_REASONS는 paneModel의 모든 에러 코드를 사람이 읽을 문구로 매핑한다", () => {
    const codes: readonly string[] = [
      "PANE_EMPTY_ID",
      "PANE_DUPLICATE_ID",
      "PANE_NOT_FOUND",
      "PANE_HEIGHT_INVALID",
      "PANE_HEIGHT_SUM_INVALID",
      "PANE_LAST_MAIN_PANE",
    ];
    for (const code of codes) {
      expect(PANE_ERROR_REASONS[code as keyof typeof PANE_ERROR_REASONS]).toEqual(expect.any(String));
      expect(PANE_ERROR_REASONS[code as keyof typeof PANE_ERROR_REASONS].length).toBeGreaterThan(0);
    }
  });

  it("빈 맵/배열 상수는 항상 비어 있다(공유 싱글턴 정체성 유지 목적)", () => {
    expect(EMPTY_OVERLAY_SERIES.size).toBe(0);
    expect(EMPTY_OVERLAY_PLOT_SPECS.size).toBe(0);
    expect(EMPTY_STRING_ARRAY).toEqual([]);
    expect(DEFAULT_TOTAL_HEIGHT).toBeGreaterThan(0);
  });
});
