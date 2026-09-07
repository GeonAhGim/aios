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
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { buildObjectTree, type ObjectTreeSource } from "@aios/chart-engine/src/legend/objectTree";
import {
  addPane,
  createPaneModel,
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
import { DataWindowPanel } from "./DataWindowPanel";
import { StatusLine } from "./StatusLine";
import {
  PLOT_ERROR_REASONS,
  buildPlotLayer,
  priceRangeFromCandles,
  priceRangeFromSeries,
  timeRangeFromCandles,
  type OverlayPlotSpecOverrides,
  type OverlaySeriesByOutput,
} from "./ChartPlotLayer";
import { useWiredCandleRenderer } from "./useVisibleCandles";

const MAIN_PANE_ID = "main";
const SURFACE_WIDTH_PX = 600;
const DEFAULT_TOTAL_HEIGHT = 420;

const PANE_ERROR_REASONS: Record<PaneModelErrorCode, string> = {
  PANE_EMPTY_ID: "페인 id가 비어 있습니다.",
  PANE_DUPLICATE_ID: "이미 존재하는 페인 id입니다.",
  PANE_NOT_FOUND: "저장된 레이아웃이 현재 서브패널 구성과 맞지 않습니다.",
  PANE_HEIGHT_INVALID: "저장된 레이아웃의 페인 높이 값이 올바르지 않습니다.",
  PANE_HEIGHT_SUM_INVALID: "저장된 레이아웃의 높이 비율 합이 1이 아닙니다.",
  PANE_LAST_MAIN_PANE: "메인 페인은 제거할 수 없습니다.",
};

function subPaneId(overlayId: string): string {
  return `sub-${overlayId}`;
}

function overlayIdFromSubPaneId(paneId: string): string {
  return paneId.slice("sub-".length);
}

function resolveTimeMsFromClientX(clientX: number, candles: readonly StreamCandle[]): number {
  if (candles.length === 0) return Date.now();
  const firstMs = candles[0]!.openTimeMs;
  const lastMs = candles[candles.length - 1]!.openTimeMs;
  const span = Math.max(lastMs - firstMs, 1);
  const fraction = Math.min(Math.max(clientX / SURFACE_WIDTH_PX, 0), 1);
  return firstMs + fraction * span;
}

function buildInitialModel(subOverlayIds: readonly string[]): PaneModel {
  return subOverlayIds.reduce((model, id) => addPane(model, subPaneId(id)), createPaneModel(MAIN_PANE_ID));
}

const EMPTY_OVERLAY_SERIES: ReadonlyMap<string, OverlaySeriesByOutput> = new Map();
const EMPTY_OVERLAY_PLOT_SPECS: ReadonlyMap<string, OverlayPlotSpecOverrides> = new Map();

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
  const [hiddenIds, setHiddenIds] = useState<ReadonlySet<string>>(new Set());
  const objectTreeSource: ObjectTreeSource = useMemo(
    () => ({
      getIndicators: () => [
        ...mainOverlays.map((o, i) => ({ id: o.id, paneId: MAIN_PANE_ID, name: o.id, visible: !hiddenIds.has(o.id), zLevel: i })),
        ...subOverlays.map((o, i) => ({
          id: o.id,
          paneId: subPaneId(o.id),
          name: o.id,
          visible: !hiddenIds.has(o.id),
          zLevel: mainOverlays.length + i,
        })),
      ],
      getOverlays: () =>
        drawings.map((d, i) => ({
          id: d.id,
          paneId: MAIN_PANE_ID,
          name: `${d.kind}:${d.id}`,
          visible: !hiddenIds.has(d.id),
          zLevel: 1000 + i,
          lock: false,
        })),
    }),
    [mainOverlays, subOverlays, drawings, hiddenIds],
  );
  const objectTree = useMemo(() => buildObjectTree(objectTreeSource), [objectTreeSource]);

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
            <div
              key={pane.id}
              data-testid={`chart-pane-${pane.id}`}
              className="relative border-b border-border last:border-b-0"
              style={{ height: rect.height }}
            >
              <div
                data-testid={`chart-pane-surface-${pane.id}`}
                className="h-full w-full"
                onMouseMove={(e) =>
                  crosshair.move({ sourcePaneId: pane.id, x: e.clientX, timeMs: resolveTimeMsFromClientX(e.clientX, candles) })
                }
                onMouseLeave={() => crosshair.hide()}
              >
                {isMain ? mainContent : (
                  <p className="p-2 text-xs text-fg-muted">서브패널 · {overlayIdFromSubPaneId(pane.id)}</p>
                )}
              </div>
              <svg
                className="pointer-events-none absolute inset-0"
                width={SURFACE_WIDTH_PX}
                height={rect.height}
                data-testid={`chart-pane-plot-${pane.id}`}
              >
                {plotLayer.nodes}
              </svg>
              {plotLayer.issues.length > 0 && (
                <div data-testid={`chart-pane-plot-error-${pane.id}`}>
                  <Alert tone="warning">
                    {plotLayer.issues.map((issue) => (
                      <p key={`${issue.overlayId}:${issue.output}`}>
                        지표 표시 실패: {issue.overlayId}.{issue.output} — {PLOT_ERROR_REASONS[issue.code]} ({issue.code})
                      </p>
                    ))}
                  </Alert>
                </div>
              )}
              <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between bg-surface/80 px-2 py-0.5 text-[11px] text-fg-secondary">
                <span data-testid={`chart-pane-ratio-${pane.id}`}>{pane.heightRatio.toFixed(6)}</span>
                <span data-testid={`chart-pane-statusline-${pane.id}`}>
                  {crosshairTimeMs !== null ? new Date(crosshairTimeMs).toISOString() : "--"}
                </span>
                {!isMain && (
                  <button
                    type="button"
                    className="pointer-events-auto"
                    aria-label={`서브패널 제거 ${overlayIdFromSubPaneId(pane.id)}`}
                    onClick={() => onRemoveSubOverlay(overlayIdFromSubPaneId(pane.id))}
                  >
                    ×
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <StatusLine candles={candles} crosshairTimeMs={crosshairTimeMs} />

      <ChartLegend
        objectTree={objectTree}
        onToggleVisible={(id) =>
          setHiddenIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          })
        }
      />

      <DataWindowPanel overlays={allOverlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={crosshairTimeMs} />
    </div>
  );
}
