// CH-14 — ChartPanes.tsx의 순수(비 React) 조각: 페인 id 코덱, 에러 문구,
// 크로스헤어 시간 환산, 초기 PaneModel 조립. 화면 배선은 ChartPanes.tsx에 남는다.
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { addPane, createPaneModel, type PaneModel, type PaneModelErrorCode } from "@aios/chart-engine/src/panes/paneModel";
import type { OverlayPlotSpecOverrides, OverlaySeriesByOutput } from "./ChartPlotLayer";

export const MAIN_PANE_ID = "main";
export const SURFACE_WIDTH_PX = 600;
export const DEFAULT_TOTAL_HEIGHT = 420;

export const PANE_ERROR_REASONS: Record<PaneModelErrorCode, string> = {
  PANE_EMPTY_ID: "페인 id가 비어 있습니다.",
  PANE_DUPLICATE_ID: "이미 존재하는 페인 id입니다.",
  PANE_NOT_FOUND: "저장된 레이아웃이 현재 서브패널 구성과 맞지 않습니다.",
  PANE_HEIGHT_INVALID: "저장된 레이아웃의 페인 높이 값이 올바르지 않습니다.",
  PANE_HEIGHT_SUM_INVALID: "저장된 레이아웃의 높이 비율 합이 1이 아닙니다.",
  PANE_LAST_MAIN_PANE: "메인 페인은 제거할 수 없습니다.",
};

export function subPaneId(overlayId: string): string {
  return `sub-${overlayId}`;
}

export function overlayIdFromSubPaneId(paneId: string): string {
  return paneId.slice("sub-".length);
}

export function resolveTimeMsFromClientX(clientX: number, candles: readonly StreamCandle[]): number {
  if (candles.length === 0) return Date.now();
  const firstMs = candles[0]!.openTimeMs;
  const lastMs = candles[candles.length - 1]!.openTimeMs;
  const span = Math.max(lastMs - firstMs, 1);
  const fraction = Math.min(Math.max(clientX / SURFACE_WIDTH_PX, 0), 1);
  return firstMs + fraction * span;
}

export function buildInitialModel(subOverlayIds: readonly string[]): PaneModel {
  return subOverlayIds.reduce((model, id) => addPane(model, subPaneId(id)), createPaneModel(MAIN_PANE_ID));
}

export const EMPTY_OVERLAY_SERIES: ReadonlyMap<string, OverlaySeriesByOutput> = new Map();
export const EMPTY_OVERLAY_PLOT_SPECS: ReadonlyMap<string, OverlayPlotSpecOverrides> = new Map();
export const EMPTY_STRING_ARRAY: readonly string[] = [];
