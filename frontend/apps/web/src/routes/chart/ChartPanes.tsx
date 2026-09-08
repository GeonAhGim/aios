// CH-14·CH-16 화면 배선 — 렌더링·툴팁 스택은 신설하지 않는다(decision). 이
// 화면이 조립하는 것: CH-14 panes/{paneModel,paneLayout,crosshairSync}.ts로
// 서브패널 CRUD·높이 비율·크로스헤어 동기화를 그대로 쓰고, CH-16
// legend/objectTree.ts 값을 ChartLegend에 그대로 넘긴다.
//
// legend/dataWindow.ts(task-2044)·legend/statusLine.ts(task-2045)는 같은 타입
// 경계 패턴을 쓴다: vendor 타입 절반(dataWindowStyle.ts/statusLineStyle.ts)은
// apps/web이 절대 임포트하지 않고, vendor-free한 절반(computeDataWindowRows/
// buildStatusLineLegends)만 각각 DataWindowPanel.tsx/StatusLine.tsx가 소비한다.
// "전 페인 statusLine 동일 timeMs" 요구는 crosshairSync.ts의 공유 시각만으로
// 충족한다 — StatusLine은 메인 페인 캔들의 OHLCV를 추가로 보여줄 뿐이다.
//
// 페인 배치 영속화(decision): 서브패널 존재 여부는 이미 CH-8로 저장되는
// selectedIndicatorIds에서 파생한다 — 새 저장 경로를 만들지 않는다. heightRatio
// 자체는 서버 왕복을 새로 만들지 않고 `restoredHeightRatios` 훅만 열어 둔다:
// paneModel.setHeightRatios가 합계 불일치를 fail-closed로 거부하는 계약을 화면이
// 무음 폴백 없이 그대로 드러낸다(DoD 3).
//
// 페인 한 칸의 DOM(서프페이스·SVG plot·statusLine 스트립)은 ChartPaneRow.tsx로,
// 순수 헬퍼(페인 id 코덱·시간 환산·초기 모델)는 chartPanesModel.ts로, legend/
// object-tree 조립은 useChartPanesObjectTree.ts로 옮겼다(P6 300줄 분할, 순수 이동).
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import {
  addPane,
  PaneModelError,
  removePane,
  setHeightRatios,
  type PaneModel,
  type PaneModelErrorCode,
} from "@aios/chart-engine/src/panes/paneModel";
import { computePaneRects } from "@aios/chart-engine/src/panes/paneLayout";
import { createCrosshairSync, type CrosshairSyncState } from "@aios/chart-engine/src/panes/crosshairSync";
import { createPriceScale, type PriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import { Alert } from "@aios/ui-web";
import { ChartLegend } from "./ChartLegend";
import { ChartPaneRow } from "./ChartPaneRow";
import { DataWindowPanel } from "./DataWindowPanel";
import { StatusLine } from "./StatusLine";
import {
  buildPlotLayer,
  priceRangeFromCandles,
  priceRangeFromSeries,
  timeRangeFromCandles,
  type OverlayPlotSpecOverrides,
  type OverlaySeriesByOutput,
} from "./ChartPlotLayer";
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
import { useChartPanesObjectTree } from "./useChartPanesObjectTree";
import { useWiredCandleRenderer } from "./useVisibleCandles";

export interface ChartPanesProps {
  /** 메인 페인 시계열 — 크로스헤어 시간 도메인 계산에 쓰인다. */
  readonly candles: readonly StreamCandle[];
  readonly mainOverlays: readonly OverlayEntry[];
  readonly subOverlays: readonly OverlayEntry[];
  readonly drawings: DrawingCollection;
  /** 서브패널의 "×"를 누르면 해당 지표를 선택 해제하도록 부모(ChartPage)에 위임한다. */
  readonly onRemoveSubOverlay: (overlayId: string) => void;
  readonly totalHeight?: number;
  /** 테스트/미래 리프용 훅 — 실제 서버 배선은 하지 않는다(decision 참고). */
  readonly restoredHeightRatios?: Readonly<Record<string, number>>;
  /** CH-15b: overlay id -> output name -> computed series (no live compute pipeline yet, same no-op-by-default hook shape as `restoredHeightRatios`; see ChartPlotLayer.tsx). */
  readonly overlaySeries?: ReadonlyMap<string, OverlaySeriesByOutput>;
  /** CH-15b: overlay id -> output name -> raw PlotSpec override, decoded fail-closed (ChartPlotLayer.tsx). Absent falls back to `deriveOverlayPlotSpec`. */
  readonly overlayPlotSpecs?: ReadonlyMap<string, OverlayPlotSpecOverrides>;
  /** CH-16b: persisted legend/object-tree order (useChartLayout.ts `objectTreeOrder`) — absent/`[]` means natural (source) order. */
  readonly objectTreeOrder?: readonly string[];
  /** CH-16b: persisted locked indicator ids (useChartLayout.ts `lockedIndicatorIds`). */
  readonly lockedIndicatorIds?: readonly string[];
  /** CH-16b: called with the next `objectTreeOrder` after a legend reorder — the caller (ChartPage) persists it via useChartLayout.ts. */
  readonly onObjectTreeOrderChange?: (order: readonly string[]) => void;
  /** CH-16b: called with the next `lockedIndicatorIds` after a legend lock toggle. */
  readonly onLockedIndicatorIdsChange?: (ids: readonly string[]) => void;
  /** 메인 페인에 그려질 실제 캔들 렌더러(CandlestickChart) — 새 렌더 스택을 만들지 않는다. */
  readonly children: ReactNode;
}

export function ChartPanes({
  candles,
  mainOverlays,
  subOverlays,
  drawings,
  onRemoveSubOverlay,
  totalHeight = DEFAULT_TOTAL_HEIGHT,
  restoredHeightRatios,
  overlaySeries = EMPTY_OVERLAY_SERIES,
  overlayPlotSpecs = EMPTY_OVERLAY_PLOT_SPECS,
  objectTreeOrder = EMPTY_STRING_ARRAY,
  lockedIndicatorIds = EMPTY_STRING_ARRAY,
  onObjectTreeOrderChange,
  onLockedIndicatorIdsChange,
  children,
}: ChartPanesProps) {
  const subOverlayIds = useMemo(() => subOverlays.map((o) => o.id), [subOverlays]);

  const [{ paneModel, layoutErrorCode }, setState] = useState<{
    paneModel: PaneModel;
    layoutErrorCode: PaneModelErrorCode | null;
  }>(() => {
    const base = buildInitialModel(subOverlayIds);
    if (!restoredHeightRatios) return { paneModel: base, layoutErrorCode: null };
    try {
      return { paneModel: setHeightRatios(base, restoredHeightRatios), layoutErrorCode: null };
    } catch (caught) {
      if (!(caught instanceof PaneModelError)) throw caught;
      return { paneModel: base, layoutErrorCode: caught.code };
    }
  });

  // 초기 마운트 이후 서브패널 구성이 바뀔 때만 반영한다 — restoredHeightRatios는 최초 1회뿐이라 여기서 다시 적용하지 않는다.
  useEffect(() => {
    setState((prev) => {
      let next = prev.paneModel;
      for (const pane of prev.paneModel.panes) {
        if (pane.kind === "sub" && !subOverlayIds.includes(overlayIdFromSubPaneId(pane.id))) {
          next = removePane(next, pane.id);
        }
      }
      for (const id of subOverlayIds) {
        if (!next.panes.some((p) => p.id === subPaneId(id))) {
          next = addPane(next, subPaneId(id));
        }
      }
      return next === prev.paneModel ? prev : { ...prev, paneModel: next };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subOverlayIds.join(",")]);

  const [crosshair] = useState(() => createCrosshairSync());
  const [crosshairState, setCrosshairState] = useState<CrosshairSyncState>(() => crosshair.snapshot());
  useEffect(() => crosshair.subscribe(setCrosshairState), [crosshair]);
  const crosshairTimeMs = crosshairState.kind === "visible" ? crosshairState.timeMs : null;

  const allOverlays = useMemo(() => [...mainOverlays, ...subOverlays], [mainOverlays, subOverlays]);
  const { objectTree, toggleHidden, handleMoveEntry, handleToggleLocked } = useChartPanesObjectTree({
    mainOverlays,
    subOverlays,
    drawings,
    objectTreeOrder,
    lockedIndicatorIds,
    onObjectTreeOrderChange,
    onLockedIndicatorIdsChange,
  });

  const rects = computePaneRects(paneModel.panes, totalHeight);
  const paneById = useMemo(() => new Map(paneModel.panes.map((p) => [p.id, p] as const)), [paneModel]);

  // CH-19c: cap the candle count reaching the render path at the surface's own pixel budget (render/lod·render/viewport: useVisibleCandles.ts).
  const candleViewport = { startTime: candles[0]?.openTimeMs ?? 0, endTime: candles[candles.length - 1]?.openTimeMs ?? 0 };
  const mainContent = useWiredCandleRenderer(children, candles, { viewport: candleViewport, targetPixelWidth: SURFACE_WIDTH_PX });

  // CH-15b: one time scale (candle domain), one price scale per pane — CH-1b core/priceScale.ts·timeScale.ts's linear-fallback mode, not a new scale concept.
  const heightOf = (paneId: string): number => rects.find((r) => r.id === paneId)?.height ?? totalHeight;
  const timeScale = createTimeScale({ range: timeRangeFromCandles(candles), width: SURFACE_WIDTH_PX });
  const mainScale = createPriceScale({ range: priceRangeFromCandles(candles), height: heightOf(MAIN_PANE_ID) });
  const subScales = new Map<string, PriceScale>(
    subOverlays.map((overlay) => {
      const paneId = subPaneId(overlay.id);
      return [paneId, createPriceScale({ range: priceRangeFromSeries(overlaySeries.get(overlay.id)), height: heightOf(paneId) })] as const;
    }),
  );

  return (
    <div className="space-y-3" data-testid="chart-panes">
      {layoutErrorCode && (
        <div data-testid="chart-panes-layout-error">
          <Alert tone="warning">
            <p>저장된 페인 레이아웃을 적용하지 못했습니다: {PANE_ERROR_REASONS[layoutErrorCode]}</p>
          </Alert>
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border">
        {rects.map((rect) => {
          const pane = paneById.get(rect.id);
          if (!pane) return null;
          const isMain = pane.kind === "main";
          const paneOverlays = isMain ? mainOverlays : subOverlays.filter((o) => subPaneId(o.id) === pane.id);
          const ownScale = isMain ? mainScale : (subScales.get(pane.id) ?? mainScale);
          // CH-15b: the actual PlotSpec-driven dispatch (ChartPlotLayer.tsx) — a new indicator's plot renders here with zero changes to this file (DoD).
          const plotLayer = buildPlotLayer({ overlays: paneOverlays, overlaySeries, overlayPlotSpecs, mainScale, ownScale, timeScale });
          return (
            <ChartPaneRow
              key={pane.id}
              paneId={pane.id}
              rectHeight={rect.height}
              heightRatio={pane.heightRatio}
              isMain={isMain}
              mainContent={mainContent}
              subLabel={overlayIdFromSubPaneId(pane.id)}
              plotLayer={plotLayer}
              crosshairTimeMs={crosshairTimeMs}
              onMouseMove={(clientX) =>
                crosshair.move({ sourcePaneId: pane.id, x: clientX, timeMs: resolveTimeMsFromClientX(clientX, candles) })
              }
              onMouseLeave={() => crosshair.hide()}
              onRemoveSubOverlay={isMain ? undefined : () => onRemoveSubOverlay(overlayIdFromSubPaneId(pane.id))}
            />
          );
        })}
      </div>

      <StatusLine candles={candles} crosshairTimeMs={crosshairTimeMs} />

      <ChartLegend
        objectTree={objectTree}
        onToggleVisible={toggleHidden}
        onMoveEntry={handleMoveEntry}
        onToggleLocked={handleToggleLocked}
      />

      <DataWindowPanel overlays={allOverlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={crosshairTimeMs} />
    </div>
  );
}
