// ChartToolbar.tsx/IndicatorPicker.tsx와 같은 이유로 배럴(@aios/chart-engine)
// 대신 vendor klinecharts 포크에 의존하지 않는 서브모듈만 직접 불러온다
// (배럴은 core/klinechartsBackend를 통해 vendor까지 재수출해 apps/web의
// 엄격한 tsconfig에서 tsc -b가 깨진다).
import type { ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { CandleQueryParams, CandleQueryResult } from "@aios/api-client";
import { ApiError, createBacktestsClient, createChartingClient, createMarketDataClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { routeApiError, type Timeframe, type Venue } from "@aios/shared-types";
import { CandlestickChart, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { BacktestPanel, type RunQuickBacktest } from "./BacktestPanel";
import { ChartPanes } from "./ChartPanes";
import { ChartToolbar } from "./ChartToolbar";
import { CompareSymbols, type CompareSymbolRef } from "./CompareSymbols";
import { ChartTemplates, type ChartTemplatesPort } from "./ChartTemplates";
import { ChartLayoutErrorBanners } from "./ChartLayoutErrorBanners";
import { buildChartLayoutControls } from "./chartToolbarLayoutProps";
import { decodeCompareSymbol, encodeCompareSymbol, NoInstrumentSelected } from "./chartPageHelpers";
import { DrawingsList } from "./DrawingsList";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";
import { IndicatorPicker } from "./IndicatorPicker";
import { StrategyMarkers } from "./StrategyMarkers";
import { useChartDrawings } from "./useChartDrawings";
import { useChartLayout, type ChartViewSnapshot } from "./useChartLayout";
import { useChartReplaySession } from "./useChartReplaySession";
import { useIndicatorSelection } from "./useIndicatorSelection";

// CH-6a — 화면 조립 리프: chart-engine의 CH-2(candleStream)·CH-3(overlayRegistry)
// ·CH-4(drawings)·CH-7(replayController) 공개 API를 이 화면에서만 소비한다.
// 서버 저장·복원(indicator 선택·drawings 영속화)은 CH-5 backend가 아직 없어
// task-1557 이후 6b 몫이다 — 여기서는 전부 로컬 state로만 관리한다.
// task-2011: 상태 소유 단위(그리기/레전드·오브젝트 트리/리플레이)는 각각
// useChartDrawings/useIndicatorSelection/useChartReplaySession으로, 순수 함수는
// chartPageHelpers.tsx로 옮겼다 — 이 파일은 화면 조립만 남긴다(순수 이동).

const VISIBLE_CANDLE_COUNT = 200;
const DEFAULT_VENUE: Venue = "BITGET";
const DEFAULT_TIMEFRAME: Timeframe = "1h";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const marketDataClient = createMarketDataClient(baseUrl, () => useAuthStore.getState().token);
const chartingClient = createChartingClient(baseUrl, () => useAuthStore.getState().token);
const backtestsClient = createBacktestsClient(baseUrl, () => useAuthStore.getState().token);

export type FetchCandles = (params: CandleQueryParams) => Promise<CandleQueryResult>;

export interface ChartPageProps {
  fetchCandles?: FetchCandles;
  // CH-6b: CH-8 레이아웃 CRUD 포트 — 테스트에서 서버 왕복 없이 주입한다(fetchCandles와 동일 관용).
  chartingPort?: ChartingPort;
  // CH-17c: CH-17b 지표 템플릿 CRUD 포트 — 같은 관용으로 주입 가능하게 둔다.
  templatesPort?: ChartTemplatesPort;
  // CH-13b: CompareSymbols의 InstrumentView 목록 조회 포트 — 같은 관용으로 주입 가능하게 둔다.
  listInstruments?: typeof marketDataClient.listInstruments;
  // BT-13: 즉시 백테스트 실행 포트 — 같은 관용으로 서버 왕복 없이 주입 가능하게 둔다.
  runQuickBacktest?: RunQuickBacktest;
  // CH-18b: IndicatorParityPanel의 화이트리스트 판정 입력(IND-12 카탈로그). 실
  // 배선(라이브 fetch)이 아직 없다 — 기본값 []는 "아무 지표도 검증 대상 아님"을
  // 정직하게 반영한다(fail-closed, IndicatorParityPanel.tsx 상단 주석 참고).
  indicatorCatalog?: readonly IndicatorCatalogEntry[];
  // CH-18b: 서버 참조 지표 시리즈 포트. 실 IND-1 계산 엔드포인트가 아직 없어
  // 기본값은 IndicatorParityPanel의 자체 기본값(항상 null)을 그대로 쓴다.
  resolveServerIndicatorSeries?: ServerIndicatorSeriesPort;
  now?: Date;
}

export function ChartPage({
  fetchCandles = marketDataClient.getCandles,
  chartingPort = chartingClient,
  templatesPort = chartingClient,
  listInstruments = marketDataClient.listInstruments,
  runQuickBacktest = backtestsClient.runQuickBacktest,
  indicatorCatalog = [],
  resolveServerIndicatorSeries,
  now,
}: ChartPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const instrumentId = searchParams.get("instrument_id");
  const [venue, setVenue] = useState<Venue>(DEFAULT_VENUE);
  const [timeframe, setTimeframe] = useState<Timeframe>(DEFAULT_TIMEFRAME);
  const [anchor] = useState(() => now ?? new Date());

  const { drawingTool, setDrawingTool, drawings, handleAddDrawing: addDrawingAt, handleRemoveDrawing } = useChartDrawings(
    venue,
    instrumentId,
    timeframe,
  );

  const {
    selectedIndicatorIds,
    setSelectedIndicatorIds,
    overlayEntries,
    knownIndicatorIds,
    selectedOverlayEntries,
    mainOverlayEntries,
    subOverlayEntries,
    appliedPaneHeightRatios,
    paneRemountKey,
    toggleIndicator,
    handleTemplateApplied,
  } = useIndicatorSelection();

  // CH-13b: 비교 심볼도 CH-8 레이아웃(useChartLayout)을 통해서만 저장·복원한다 —
  // 이 화면은 구조체로, 훅은 "VENUE:instrumentId" 문자열로 다룬다(encode/decode 경계).
  const [compareSymbols, setCompareSymbols] = useState<CompareSymbolRef[]>([]);

  // CH-6b: 현재 화면(venue/timeframe/instrumentId/지표/비교 심볼)을 CH-8 레이아웃의
  // 활성 패널과 양방향으로 거울처럼 맞춘다 — 실제 복원·저장·충돌 판정은 useChartLayout 소관.
  const layoutView: ChartViewSnapshot = useMemo(
    () => ({
      instrumentId: instrumentId ?? "",
      venue,
      timeframe,
      indicatorIds: selectedIndicatorIds,
      compareSymbolIds: compareSymbols.map(encodeCompareSymbol),
    }),
    [instrumentId, venue, timeframe, selectedIndicatorIds, compareSymbols],
  );
  const layout = useChartLayout({
    port: chartingPort,
    enabled: instrumentId !== null,
    view: layoutView,
    onApplyView: (v) => {
      setTimeframe(v.timeframe as Timeframe);
      setSelectedIndicatorIds([...v.indicatorIds]);
      setCompareSymbols(v.compareSymbolIds.map(decodeCompareSymbol).filter((r): r is CompareSymbolRef => r !== null));
      if (v.instrumentId && v.instrumentId !== instrumentId) setSearchParams({ instrument_id: v.instrumentId });
      if (v.venue !== venue) setVenue(v.venue as Venue);
    },
  });

  const end = anchor.toISOString();
  const TIMEFRAME_MS: Record<Timeframe, number> = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
  };
  const start = new Date(anchor.getTime() - VISIBLE_CANDLE_COUNT * TIMEFRAME_MS[timeframe]).toISOString();

  const query = useQuery({
    queryKey: ["chart-candles", venue, instrumentId, timeframe, start, end],
    queryFn: () => fetchCandles({ venue, instrumentId: instrumentId as string, timeframe, start, end }),
    enabled: instrumentId !== null && instrumentId.trim().length > 0,
  });

  const { replayRef, replayState, displayCandles, points, replayDisabled } = useChartReplaySession({
    venue,
    instrumentId,
    timeframe,
    queryData: query.data,
  });

  if (!instrumentId) {
    return <NoInstrumentSelected />;
  }

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";
  // CH-13b: CompareSymbols는 CH-2 candleStream(재생용 파생 상태)이 아니라 이 조회
  // 응답의 원본 캔들을 기준 시리즈로 쓴다(align/normalize/spread 입력 계약과 동일).
  const baseSeries = query.data?.series;
  const baseCandles = baseSeries?.kind === "ok" ? baseSeries.value.candles : [];

  function handleAddDrawing(): void {
    addDrawingAt(displayCandles[displayCandles.length - 1]);
  }

  return (
    <AppShell>
      <div className="max-w-5xl space-y-4">
        <PageHeader title="차트" />

        <div className="flex items-end gap-3">
          <p className="px-3 py-2 text-sm text-fg" data-testid="chart-instrument-id">
            {instrumentId}
          </p>
        </div>

        <ChartLayoutErrorBanners layout={layout} />

        <ChartToolbar
          venue={venue}
          onVenueChange={setVenue}
          timeframe={timeframe}
          onTimeframeChange={setTimeframe}
          drawingTool={drawingTool}
          onDrawingToolChange={setDrawingTool}
          onAddDrawing={handleAddDrawing}
          addDrawingDisabled={drawingTool === null || displayCandles.length === 0}
          replayStatus={replayState.status}
          replaySpeed={replayState.speed}
          replayDisabled={replayDisabled}
          onPlay={() => replayRef.current?.play()}
          onPause={() => replayRef.current?.pause()}
          onStep={(delta) => replayRef.current?.step(delta)}
          onSpeedChange={(speed) => replayRef.current?.setSpeed(speed)}
          instrumentId={instrumentId}
          selectedIndicatorIds={selectedIndicatorIds}
          currentClose={points.length > 0 ? points[points.length - 1]!.close : null}
          layout={buildChartLayoutControls(layout, instrumentId, venue)}
        />

        <div className="flex items-start gap-2">
          <IndicatorPicker available={overlayEntries} selectedIds={selectedIndicatorIds} onToggle={toggleIndicator} />
          <ChartTemplates
            port={templatesPort}
            mainIndicatorIds={mainOverlayEntries.map((e) => e.id)}
            subIndicatorIds={subOverlayEntries.map((e) => e.id)}
            knownIndicatorIds={knownIndicatorIds}
            onApplied={handleTemplateApplied}
          />
        </div>

        {query.isError ? (
          <ErrorMessage
            errorCode={query.error instanceof ApiError ? query.error.errorCode : undefined}
            message={query.error instanceof Error ? query.error.message : undefined}
            traceId={query.error instanceof ApiError ? query.error.traceId : undefined}
            retryAfterSec={routed?.kind === "backoff_retry" ? routed.afterSec : undefined}
            onRetry={canRetry ? () => query.refetch() : undefined}
          />
        ) : query.isLoading ? (
          <LoadingState />
        ) : points.length === 0 ? (
          <EmptyState>표시할 캔들이 없습니다.</EmptyState>
        ) : (
          <ChartPanes
            key={paneRemountKey}
            candles={displayCandles}
            mainOverlays={mainOverlayEntries}
            subOverlays={subOverlayEntries}
            drawings={drawings}
            onRemoveSubOverlay={toggleIndicator}
            restoredHeightRatios={appliedPaneHeightRatios}
          >
            <CandlestickChart data={points} />
          </ChartPanes>
        )}

        <IndicatorParityPanel
          candles={displayCandles}
          overlays={selectedOverlayEntries}
          catalog={indicatorCatalog}
          resolveServerSeries={resolveServerIndicatorSeries}
        />

        <StrategyMarkers instrumentId={instrumentId} points={points} />

        <BacktestPanel
          venue={venue}
          instrumentId={instrumentId}
          timeframe={timeframe}
          start={start}
          end={end}
          points={points}
          runQuickBacktest={runQuickBacktest}
        />

        <CompareSymbols
          baseVenue={venue}
          baseInstrumentId={instrumentId}
          baseCandles={baseCandles}
          timeframe={timeframe}
          start={start}
          end={end}
          compareSymbols={compareSymbols}
          onAdd={(ref) => setCompareSymbols((prev) => [...prev, ref])}
          onRemove={(ref) =>
            setCompareSymbols((prev) => prev.filter((s) => !(s.venue === ref.venue && s.instrumentId === ref.instrumentId)))
          }
          fetchCandles={fetchCandles}
          listInstruments={listInstruments}
        />

        <DrawingsList drawings={drawings} onRemove={handleRemoveDrawing} />
      </div>
    </AppShell>
  );
}
