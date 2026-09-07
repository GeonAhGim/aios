// CH-6b 순수 이동(task-2011): useChartLayout.ts의 "뷰 ↔ 패널" 순수 변환 함수만 그대로
// 옮긴다 — 로직 변경 없음.
import type { ChartPanel, IndicatorRef, InstrumentRef } from "@aios/chart-engine/src/layout/layoutModel";

export interface ChartViewSnapshot {
  readonly instrumentId: string;
  readonly venue: string;
  readonly timeframe: string;
  readonly indicatorIds: readonly string[];
  /** CH-13b 비교 심볼("VENUE:instrumentId"). layoutModel.ts 스키마는 그대로 두고 panel.indicators에 접두어를 붙여 함께 저장한다. */
  readonly compareSymbolIds: readonly string[];
}

const COMPARE_SYMBOL_PREFIX = "compare:";

export function panelViewFields(view: ChartViewSnapshot): Pick<ChartPanel, "instrument" | "timeframe" | "indicators"> {
  return {
    instrument: { instrumentId: view.instrumentId, venue: view.venue, symbol: view.instrumentId },
    timeframe: view.timeframe,
    indicators: [
      ...view.indicatorIds.map((id): IndicatorRef => ({ id })),
      ...view.compareSymbolIds.map((id): IndicatorRef => ({ id: `${COMPARE_SYMBOL_PREFIX}${id}` })),
    ],
  };
}

export function panelToView(panel: ChartPanel): ChartViewSnapshot {
  const ids = panel.indicators.map((i) => i.id);
  return {
    instrumentId: panel.instrument.instrumentId,
    venue: panel.instrument.venue,
    timeframe: panel.timeframe,
    indicatorIds: ids.filter((id) => !id.startsWith(COMPARE_SYMBOL_PREFIX)),
    compareSymbolIds: ids.filter((id) => id.startsWith(COMPARE_SYMBOL_PREFIX)).map((id) => id.slice(COMPARE_SYMBOL_PREFIX.length)),
  };
}

export function sameView(panel: ChartPanel, view: ChartViewSnapshot): boolean {
  const v = panelToView(panel);
  return (
    v.instrumentId === view.instrumentId &&
    v.venue === view.venue &&
    v.timeframe === view.timeframe &&
    v.indicatorIds.join(",") === view.indicatorIds.join(",") &&
    v.compareSymbolIds.join(",") === view.compareSymbolIds.join(",")
  );
}

export function sameInstrument(a: InstrumentRef, b: InstrumentRef): boolean {
  return a.instrumentId === b.instrumentId && a.venue === b.venue;
}
