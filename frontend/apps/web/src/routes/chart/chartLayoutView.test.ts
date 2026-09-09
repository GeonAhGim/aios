import { describe, expect, it } from "vitest";
import type { ChartPanel } from "@aios/chart-engine/src/layout/layoutModel";
import { panelToView, panelViewFields, sameInstrument, sameView, type ChartViewSnapshot } from "./chartLayoutView";

// CH-6b 순수 이동(task-2011): useChartLayout.ts의 "뷰 ↔ 패널" 순수 변환 함수만
// 옮긴 것 — 로직 변경 없음. useChartLayout.test.ts가 이 변환을 배선한 훅
// 레벨에서 이미 다루지만, 이 순수 조각 자체의 왕복 변환·비교 계약은 여기서
// 직접 확인한다.

function view(overrides: Partial<ChartViewSnapshot> = {}): ChartViewSnapshot {
  return {
    instrumentId: "BTCUSDT",
    venue: "BITGET",
    timeframe: "1h",
    indicatorIds: ["SMA", "RSI"],
    compareSymbolIds: [],
    ...overrides,
  };
}

function panel(overrides: Partial<ChartPanel> = {}): ChartPanel {
  return {
    id: "p1",
    instrument: { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" },
    timeframe: "1h",
    indicators: [{ id: "SMA" }, { id: "RSI" }],
    drawingSetId: "p1",
    ...overrides,
  };
}

describe("chartLayoutView — panelViewFields", () => {
  it("instrument/timeframe/indicators를 뷰에서 그대로 옮기고, compareSymbolIds는 compare: 접두어로 합쳐진다", () => {
    const fields = panelViewFields(view({ compareSymbolIds: ["ETHUSDT"] }));

    expect(fields.instrument).toEqual({ instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" });
    expect(fields.timeframe).toBe("1h");
    expect(fields.indicators).toEqual([{ id: "SMA" }, { id: "RSI" }, { id: "compare:ETHUSDT" }]);
  });

  it("compareSymbolIds가 비어 있으면 indicators는 indicatorIds만 반영한다", () => {
    const fields = panelViewFields(view());
    expect(fields.indicators).toEqual([{ id: "SMA" }, { id: "RSI" }]);
  });
});

describe("chartLayoutView — panelToView", () => {
  it("일반 지표와 compare: 접두 지표를 각각 indicatorIds/compareSymbolIds로 분리한다", () => {
    const p = panel({ indicators: [{ id: "SMA" }, { id: "compare:ETHUSDT" }, { id: "RSI" }] });

    const result = panelToView(p);

    expect(result).toEqual({
      instrumentId: "BTCUSDT",
      venue: "BITGET",
      timeframe: "1h",
      indicatorIds: ["SMA", "RSI"],
      compareSymbolIds: ["ETHUSDT"],
    });
  });

  it("compare: 접두 지표가 없으면 compareSymbolIds는 빈 배열이다", () => {
    const result = panelToView(panel());
    expect(result.compareSymbolIds).toEqual([]);
  });
});

describe("chartLayoutView — panelToView(panelViewFields(view)) 왕복", () => {
  it("뷰 → 패널 필드 → 뷰 변환은 원래 뷰와 동일한 정보를 보존한다", () => {
    const original = view({ compareSymbolIds: ["ETHUSDT", "SOLUSDT"] });
    const fields = panelViewFields(original);
    const roundTripped = panelToView({ id: "p1", drawingSetId: "p1", ...fields });

    expect(roundTripped).toEqual(original);
  });
});

describe("chartLayoutView — sameView", () => {
  it("instrument/venue/timeframe/indicatorIds/compareSymbolIds가 모두 같으면 true다", () => {
    const p = panel();
    expect(sameView(p, view())).toBe(true);
  });

  it("indicatorIds 순서가 다르면 false다(join 비교는 순서에 민감)", () => {
    const p = panel({ indicators: [{ id: "RSI" }, { id: "SMA" }] });
    expect(sameView(p, view())).toBe(false);
  });

  it("compareSymbolIds가 다르면 false다", () => {
    const p = panel({ indicators: [{ id: "SMA" }, { id: "RSI" }, { id: "compare:ETHUSDT" }] });
    expect(sameView(p, view())).toBe(false);
  });

  it("timeframe이 다르면 false다", () => {
    const p = panel({ timeframe: "4h" });
    expect(sameView(p, view())).toBe(false);
  });
});

describe("chartLayoutView — sameInstrument", () => {
  it("instrumentId와 venue가 모두 같으면 true다(symbol은 비교하지 않음)", () => {
    expect(
      sameInstrument(
        { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" },
        { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "다른표기" },
      ),
    ).toBe(true);
  });

  it("venue가 다르면 false다", () => {
    expect(
      sameInstrument(
        { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" },
        { instrumentId: "BTCUSDT", venue: "BINANCE", symbol: "BTCUSDT" },
      ),
    ).toBe(false);
  });

  it("instrumentId가 다르면 false다", () => {
    expect(
      sameInstrument(
        { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" },
        { instrumentId: "ETHUSDT", venue: "BITGET", symbol: "ETHUSDT" },
      ),
    ).toBe(false);
  });
});
