// ChartToolbar.tsx/IndicatorPicker.tsx와 같은 이유로 배럴(@aios/chart-engine)
// 대신 vendor klinecharts 포크에 의존하지 않는 서브모듈만 직접 불러온다
// (배럴은 core/klinechartsBackend를 통해 vendor까지 재수출해 apps/web의
// 엄격한 tsconfig에서 tsc -b가 깨진다).
import { createCandleStream, type CandleStream, type CandleStreamSnapshot, type StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import {
  addDrawing,
  createFibonacci,
  createHorizontalLine,
  createRectangle,
  createTrendLine,
  createVerticalLine,
  removeDrawing,
} from "@aios/chart-engine/src/drawings/tools";
import type { Drawing, DrawingCollection, DrawingKind } from "@aios/chart-engine/src/drawings/model";
import { createDefaultOverlayRegistry, type OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import {
  createReplayController,
  type ReplayClock,
  type ReplayController,
  type ReplayFrame,
  type ReplayState,
} from "@aios/chart-engine/src/replay/replayController";
import type { ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import type { CandleQueryParams, CandleQueryResult } from "@aios/api-client";
import { ApiError, createChartingClient, createMarketDataClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { routeApiError, type SeriesKey, type Timeframe, type Venue } from "@aios/shared-types";
import { CandlestickChart, type CandlestickPoint, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ChartToolbar } from "./ChartToolbar";
import { IndicatorPicker } from "./IndicatorPicker";
import { StrategyMarkers } from "./StrategyMarkers";
import { useChartLayout, type ChartViewSnapshot } from "./useChartLayout";

// CH-6a — 화면 조립 리프: chart-engine의 CH-2(candleStream)·CH-3(overlayRegistry)
// ·CH-4(drawings)·CH-7(replayController) 공개 API를 이 화면에서만 소비한다.
// 서버 저장·복원(indicator 선택·drawings 영속화)은 CH-5 backend가 아직 없어
// task-1557 이후 6b 몫이다 — 여기서는 전부 로컬 state로만 관리한다.

const VISIBLE_CANDLE_COUNT = 200;
const DEFAULT_VENUE: Venue = "BITGET";
const DEFAULT_TIMEFRAME: Timeframe = "1h";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const marketDataClient = createMarketDataClient(baseUrl, () => useAuthStore.getState().token);
const chartingClient = createChartingClient(baseUrl, () => useAuthStore.getState().token);

export type FetchCandles = (params: CandleQueryParams) => Promise<CandleQueryResult>;

interface ChartPageProps {
  fetchCandles?: FetchCandles;
  // CH-6b: CH-8 레이아웃 CRUD 포트 — 테스트에서 서버 왕복 없이 주입한다(fetchCandles와 동일 관용).
  chartingPort?: ChartingPort;
  now?: Date;
}

function toChartPoints(candles: readonly StreamCandle[]): CandlestickPoint[] {
  return candles.map((c) => ({
    time: Math.floor(c.openTimeMs / 1000),
    open: Number(c.record.open),
    high: Number(c.record.high),
    low: Number(c.record.low),
    close: Number(c.record.close),
  }));
}

function drawingLabel(d: Drawing): string {
  switch (d.kind) {
    case "trendline":
      return `추세선 (${d.points[0].time}→${d.points[1].time})`;
    case "horizontal-line":
      return `수평선 @${d.price}`;
    case "vertical-line":
      return `수직선 @${d.time}`;
    case "rectangle":
      return `사각형 (${d.points[0].time}→${d.points[1].time})`;
    case "fibonacci":
      return `피보나치 (${d.points[0].time}→${d.points[1].time})`;
  }
}

function createDrawing(id: string, kind: DrawingKind, time: number, price: number): Drawing {
  switch (kind) {
    case "horizontal-line":
      return createHorizontalLine(id, price);
    case "vertical-line":
      return createVerticalLine(id, time);
    case "trendline":
      return createTrendLine(id, { time: time - 1, price }, { time, price });
    case "rectangle":
      return createRectangle(id, { time: time - 1, price: price * 0.99 }, { time, price: price * 1.01 });
    case "fibonacci":
      return createFibonacci(id, { time: time - 1, price: price * 0.99 }, { time, price });
  }
}

function NoInstrumentSelected() {
  return (
    <AppShell>
      <div className="max-w-5xl space-y-4">
        <PageHeader title="차트" />
        <EmptyState>
          심볼을 먼저 선택하세요.{" "}
          <Link to="/market/instruments" className="underline">
            심볼 목록으로 이동
          </Link>
        </EmptyState>
      </div>
    </AppShell>
  );
}

const REAL_CLOCK: ReplayClock = {
  setTimeout: (cb, ms) => window.setTimeout(cb, ms),
  clearTimeout: (t) => window.clearTimeout(t as number),
};

// replayController가 아직 한 번도 프레임을 내보내기 전(마운트 직후·스트림
// 교체 직후)의 표시용 기본값 — ref.current를 렌더 중에 읽지 않기 위한 정적 값.
const IDLE_REPLAY_STATE: ReplayState = {
  status: "paused",
  speed: 1,
  cursorTs: null,
  visibleCount: 0,
  totalCount: 0,
  atEnd: true,
};

export function ChartPage({
  fetchCandles = marketDataClient.getCandles,
  chartingPort = chartingClient,
  now,
}: ChartPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const instrumentId = searchParams.get("instrument_id");
  const [venue, setVenue] = useState<Venue>(DEFAULT_VENUE);
  const [timeframe, setTimeframe] = useState<Timeframe>(DEFAULT_TIMEFRAME);
  const [anchor] = useState(() => now ?? new Date());

  const [snapshot, setSnapshot] = useState<CandleStreamSnapshot | null>(null);
  const [replayFrame, setReplayFrame] = useState<ReplayFrame | null>(null);
  const streamRef = useRef<CandleStream | null>(null);
  const replayRef = useRef<ReplayController | null>(null);

  const [drawingTool, setDrawingTool] = useState<DrawingKind | null>(null);
  const [drawings, setDrawings] = useState<DrawingCollection>([]);
  const drawingSeq = useRef(0);

  const [selectedIndicatorIds, setSelectedIndicatorIds] = useState<string[]>([]);
  const overlayEntries: readonly OverlayEntry[] = useMemo(() => createDefaultOverlayRegistry().list(), []);

  // CH-6b: 현재 화면(venue/timeframe/instrumentId/지표)을 CH-8 레이아웃의 활성 패널과
  // 양방향으로 거울처럼 맞춘다 — 실제 복원·저장·충돌 판정은 useChartLayout 소관.
  const layoutView: ChartViewSnapshot = useMemo(
    () => ({ instrumentId: instrumentId ?? "", venue, timeframe, indicatorIds: selectedIndicatorIds }),
    [instrumentId, venue, timeframe, selectedIndicatorIds],
  );
  const layout = useChartLayout({
    port: chartingPort,
    enabled: instrumentId !== null,
    view: layoutView,
    onApplyView: (v) => {
      setTimeframe(v.timeframe as Timeframe);
      setSelectedIndicatorIds([...v.indicatorIds]);
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

  // 심볼/거래소/타임프레임이 바뀔 때마다 CH-2 candleStream과 CH-7
  // replayController를 새로 만든다 — 서로 다른 키의 봉을 한 스트림에
  // 섞지 않는다(candleStream.ts key_mismatch 규약).
  useEffect(() => {
    if (!instrumentId) return undefined;
    const key: SeriesKey = { venue, instrument_id: instrumentId, timeframe };
    const stream = createCandleStream({ key });
    const replay = createReplayController(stream, { clock: REAL_CLOCK });
    streamRef.current = stream;
    replayRef.current = replay;
    setDrawings([]);
    setSnapshot(stream.snapshot());
    setReplayFrame(null);
    const unsubStream = stream.subscribe(setSnapshot);
    const unsubReplay = replay.subscribe(setReplayFrame);
    return () => {
      unsubStream();
      unsubReplay();
      replay.dispose();
      stream.dispose();
      streamRef.current = null;
      replayRef.current = null;
    };
  }, [venue, instrumentId, timeframe]);

  // 서버에서 새 페이지가 도착할 때마다 같은 스트림에 병합한다(applyPage가
  // 중복·역행·gap 판정을 전담 — 여기서는 결과를 재정렬하지 않는다).
  useEffect(() => {
    const series = query.data?.series;
    if (streamRef.current && series) streamRef.current.applyPage(series);
  }, [query.data]);

  if (!instrumentId) {
    return <NoInstrumentSelected />;
  }

  const replayState = replayFrame?.state ?? IDLE_REPLAY_STATE;
  const replayEngaged = replayState.status === "playing" || replayState.cursorTs !== null;
  const displayCandles = replayEngaged ? (replayFrame?.visible ?? []) : (snapshot?.candles ?? []);
  const points = toChartPoints(displayCandles);
  const replayDisabled = (snapshot?.candles.length ?? 0) === 0;

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";

  function handleAddDrawing(): void {
    if (!drawingTool) return;
    const last = displayCandles[displayCandles.length - 1];
    if (!last) return;
    const time = Math.floor(last.openTimeMs / 1000);
    const price = Number(last.record.close);
    drawingSeq.current += 1;
    const drawing = createDrawing(`drawing-${drawingSeq.current}`, drawingTool, time, price);
    setDrawings((prev) => addDrawing(prev, drawing));
  }

  function handleRemoveDrawing(id: string): void {
    setDrawings((prev) => removeDrawing(prev, id));
  }

  function toggleIndicator(id: string): void {
    setSelectedIndicatorIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  const restoreRouted = layout.restoreError ? routeApiError(layout.restoreError) : null;
  const saveRouted = layout.saveStatus === "error" ? routeApiError(layout.saveError) : null;

  return (
    <AppShell>
      <div className="max-w-5xl space-y-4">
        <PageHeader title="차트" />

        <div className="flex items-end gap-3">
          <p className="px-3 py-2 text-sm text-fg" data-testid="chart-instrument-id">
            {instrumentId}
          </p>
        </div>

        {layout.status === "restore_failed" && (
          <ErrorMessage
            errorCode={layout.restoreError instanceof ApiError ? layout.restoreError.errorCode : undefined}
            message={layout.restoreError instanceof Error ? layout.restoreError.message : undefined}
            traceId={layout.restoreError instanceof ApiError ? layout.restoreError.traceId : undefined}
            retryAfterSec={restoreRouted?.kind === "backoff_retry" ? restoreRouted.afterSec : undefined}
            onRetry={
              restoreRouted?.kind === "refetch_retry" || restoreRouted?.kind === "backoff_retry"
                ? layout.retryRestore
                : undefined
            }
          />
        )}
        {saveRouted && (
          <ErrorMessage
            errorCode={layout.saveError instanceof ApiError ? layout.saveError.errorCode : undefined}
            message={layout.saveError instanceof Error ? layout.saveError.message : undefined}
            traceId={layout.saveError instanceof ApiError ? layout.saveError.traceId : undefined}
            retryAfterSec={saveRouted.kind === "backoff_retry" ? saveRouted.afterSec : undefined}
            onRetry={saveRouted.kind === "refetch_retry" || saveRouted.kind === "backoff_retry" ? layout.save : undefined}
          />
        )}

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
          layout={{
            name: layout.layoutName,
            onNameChange: layout.rename,
            onSave: () => void layout.save(),
            onDelete: () => void layout.remove(),
            saveStatus: layout.saveStatus,
            onReload: () => void layout.reload(),
            panels: layout.model.panels.map((p) => ({ id: p.id, label: `${p.instrument.instrumentId} · ${p.timeframe}` })),
            activePanelId: layout.model.activePanelId,
            onSelectPanel: layout.setActivePanel,
            onAddPanel: layout.addPanel,
            onRemovePanel: () => {
              if (layout.model.activePanelId) layout.removePanel(layout.model.activePanelId);
            },
            isWatchlisted: layout.model.watchlists.some((w) =>
              w.entries.some((e) => e.instrumentId === instrumentId && e.venue === venue),
            ),
            onToggleWatchlist: () => layout.toggleWatchlistEntry({ instrumentId, venue, symbol: instrumentId }),
          }}
        />

        <IndicatorPicker available={overlayEntries} selectedIds={selectedIndicatorIds} onToggle={toggleIndicator} />

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
          <CandlestickChart data={points} />
        )}

        <StrategyMarkers instrumentId={instrumentId} points={points} />

        <section aria-label="그리기 목록" className="space-y-1.5">
          <h2 className="text-sm font-medium text-fg-secondary">그리기 ({drawings.length})</h2>
          {drawings.length > 0 && (
            <ul className="space-y-1">
              {drawings.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-2 text-sm text-fg">
                  <span>{drawingLabel(d)}</span>
                  <button
                    type="button"
                    className="text-xs text-danger underline"
                    onClick={() => handleRemoveDrawing(d.id)}
                  >
                    삭제
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}
