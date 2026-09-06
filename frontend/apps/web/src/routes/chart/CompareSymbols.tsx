// CH-13b — CH-13a(task-1616) align/normalize/spread 순수 계산만 소비한다(재구현
// 금지). 심볼은 InstrumentView 목록에서 고른다(task-708/1088 선례, 자유입력
// 금지 — task-1130). 에러는 routeApiError→ErrorMessage 단일 경로로만 노출한다.
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { CandleQueryParams, CandleQueryResult, InstrumentListParams, InstrumentListResult } from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import { AlignError, alignSeries } from "@aios/chart-engine/src/compare/align";
import { NormalizeError, normalizeToBase100, type NormalizedPoint } from "@aios/chart-engine/src/compare/normalize";
import { SpreadError, spread, type SpreadPoint } from "@aios/chart-engine/src/compare/spread";
import { createOverlayRegistry, DEFAULT_OVERLAY_DEFINITIONS, MAIN_PANE_INDEX } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { routeApiError, type CandleRecord, type Timeframe, type Venue } from "@aios/shared-types";
import { Button, CATEGORICAL_PALETTE, EmptyState, LoadingState, Select } from "@aios/ui-web";
import { ErrorMessage } from "../../components/ErrorMessage";

export interface CompareSymbolRef {
  readonly instrumentId: string;
  readonly venue: Venue;
}

export type FetchCompareCandles = (params: CandleQueryParams) => Promise<CandleQueryResult>;
export type ListCompareInstruments = (params?: InstrumentListParams) => Promise<InstrumentListResult>;

export interface CompareSymbolsProps {
  readonly baseVenue: Venue;
  readonly baseInstrumentId: string;
  readonly baseCandles: readonly CandleRecord[];
  readonly timeframe: Timeframe;
  readonly start: string;
  readonly end: string;
  readonly compareSymbols: readonly CompareSymbolRef[];
  readonly onAdd: (ref: CompareSymbolRef) => void;
  readonly onRemove: (ref: CompareSymbolRef) => void;
  readonly fetchCandles: FetchCompareCandles;
  readonly listInstruments: ListCompareInstruments;
}

const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const COMPARE_SPREAD_ID = "COMPARE_SPREAD";

// CH-3 overlayRegistry(task-1557)의 기존 등록·해석 API만 통해 스프레드 페인의
// placement/series 메타데이터를 얻는다 — 새 페인 배치 규칙을 여기서 만들지 않는다.
function spreadPaneEntry() {
  return createOverlayRegistry([
    ...DEFAULT_OVERLAY_DEFINITIONS,
    { id: COMPARE_SPREAD_ID, placement: "sub-pane" as const, params: [], outputs: [{ name: "value", series: "line" as const }] },
  ]).resolve(COMPARE_SPREAD_ID);
}

/** 기준 심볼과 동일하거나 이미 추가된 비교 심볼은 거부한다(DoD: 중복 추가 거부). */
export function isDuplicateCompareSymbol(
  existing: readonly CompareSymbolRef[],
  baseVenue: Venue,
  baseInstrumentId: string,
  candidate: CompareSymbolRef,
): boolean {
  if (candidate.venue === baseVenue && candidate.instrumentId === baseInstrumentId) return true;
  return existing.some((e) => e.venue === candidate.venue && e.instrumentId === candidate.instrumentId);
}

type SparkPoint = { readonly timeMs: number; readonly value: number | null };
type ComparisonOutcome =
  | { kind: "empty" }
  | { kind: "scale_mismatch" }
  | { kind: "ok"; overlayBase: readonly NormalizedPoint[]; overlayOther: readonly NormalizedPoint[]; spreadPoints: readonly SpreadPoint[] };

/** CH-13a 계산만 오케스트레이션한다 — align/normalize/spread 자체 로직은 여기서 다시 만들지 않는다. */
export function computeComparison(base: readonly CandleRecord[], other: readonly CandleRecord[]): ComparisonOutcome {
  if (base.length === 0 || other.length === 0) return { kind: "empty" };
  let aligned;
  try {
    aligned = alignSeries(base, other);
  } catch (err) {
    if (err instanceof AlignError) return { kind: "empty" };
    throw err;
  }
  const anchor = aligned.points.find((p) => p.base && p.other);
  if (!anchor) return { kind: "empty" };
  try {
    const overlayBase = normalizeToBase100(base, anchor.timeMs);
    const overlayOther = normalizeToBase100(other, anchor.timeMs);
    const spreadPoints = spread(aligned, "ratio");
    return { kind: "ok", overlayBase, overlayOther, spreadPoints };
  } catch (err) {
    if (err instanceof NormalizeError) return { kind: "empty" };
    if (err instanceof SpreadError && err.code === "quote_scale_mismatch") return { kind: "scale_mismatch" };
    throw err;
  }
}

const SPARK_W = 300;
const SPARK_H = 56;

function polylinePoints(points: readonly SparkPoint[], min: number, max: number): string {
  const usable = points.filter((p): p is { timeMs: number; value: number } => p.value !== null);
  if (usable.length === 0) return "";
  const span = max - min || 1;
  const t0 = usable[0]!.timeMs;
  const tSpan = usable[usable.length - 1]!.timeMs - t0 || 1;
  return usable
    .map((p) => `${(((p.timeMs - t0) / tSpan) * SPARK_W).toFixed(1)},${(SPARK_H - ((p.value - min) / span) * SPARK_H).toFixed(1)}`)
    .join(" ");
}

function Sparkline({ series, label }: { series: readonly { label: string; color: string; points: readonly SparkPoint[] }[]; label: string }) {
  const values = series.flatMap((s) => s.points.map((p) => p.value).filter((v): v is number => v !== null));
  const min = values.length > 0 ? Math.min(...values) : 0;
  const max = values.length > 0 ? Math.max(...values) : 1;
  return (
    <svg viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} className="h-14 w-full" role="img" aria-label={label}>
      {series.map((s) => (
        <polyline key={s.label} points={polylinePoints(s.points, min, max)} fill="none" stroke={s.color} strokeWidth={1.5} />
      ))}
    </svg>
  );
}

interface AddSymbolFormProps {
  readonly baseVenue: Venue;
  readonly baseInstrumentId: string;
  readonly existing: readonly CompareSymbolRef[];
  readonly listInstruments: ListCompareInstruments;
  readonly onAdd: (ref: CompareSymbolRef) => void;
}

function AddSymbolForm({ baseVenue, baseInstrumentId, existing, listInstruments, onAdd }: AddSymbolFormProps) {
  const [open, setOpen] = useState(false);
  const [venue, setVenue] = useState<Venue>(baseVenue);
  const [instrumentId, setInstrumentId] = useState("");
  const [rejected, setRejected] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["compare-instrument-picker", venue],
    queryFn: () => listInstruments({ venue }),
    enabled: open,
  });

  const options = (query.data?.items ?? [])
    .filter((p) => p.kind === "ok")
    .map((p) => (p as Extract<typeof p, { kind: "ok" }>).value)
    .filter((v) => !isDuplicateCompareSymbol(existing, baseVenue, baseInstrumentId, { instrumentId: v.instrument_id, venue: v.venue }));

  function handleAdd(): void {
    if (!instrumentId) return;
    const candidate: CompareSymbolRef = { instrumentId, venue };
    if (isDuplicateCompareSymbol(existing, baseVenue, baseInstrumentId, candidate)) {
      setRejected(`${candidate.instrumentId}은(는) 이미 기준/비교 심볼입니다.`);
      return;
    }
    setRejected(null);
    onAdd(candidate);
    setInstrumentId("");
  }

  if (!open) {
    return (
      <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(true)}>
        비교 심볼 추가
      </Button>
    );
  }

  return (
    <div className="flex flex-wrap items-end gap-2" data-testid="compare-add-form">
      <Select aria-label="비교 심볼 venue" value={venue} onChange={(e) => { setVenue(e.target.value as Venue); setInstrumentId(""); }}>
        {VENUES.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </Select>
      {query.isLoading ? (
        <LoadingState />
      ) : (
        <Select aria-label="비교 심볼 선택" value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
          <option value="">심볼 선택</option>
          {options.map((v) => (
            <option key={v.instrument_id} value={v.instrument_id}>
              {v.instrument_id}
            </option>
          ))}
        </Select>
      )}
      <Button type="button" size="sm" disabled={!instrumentId} onClick={handleAdd}>
        추가
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
        취소
      </Button>
      {rejected && <p className="w-full text-xs text-danger">{rejected}</p>}
    </div>
  );
}

interface CompareSymbolPaneProps {
  readonly symbolRef: CompareSymbolRef;
  readonly baseInstrumentId: string;
  readonly baseCandles: readonly CandleRecord[];
  readonly timeframe: Timeframe;
  readonly start: string;
  readonly end: string;
  readonly fetchCandles: FetchCompareCandles;
  readonly onRemove: () => void;
}

function CompareSymbolPane({ symbolRef, baseInstrumentId, baseCandles, timeframe, start, end, fetchCandles, onRemove }: CompareSymbolPaneProps) {
  const query = useQuery({
    queryKey: ["compare-candles", symbolRef.venue, symbolRef.instrumentId, timeframe, start, end],
    queryFn: () => fetchCandles({ venue: symbolRef.venue, instrumentId: symbolRef.instrumentId, timeframe, start, end }),
  });

  let body: ReactNode;
  if (query.isError) {
    const routed = routeApiError(query.error);
    const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
    body = (
      <ErrorMessage
        errorCode={query.error instanceof ApiError ? query.error.errorCode : undefined}
        message={query.error instanceof Error ? query.error.message : undefined}
        traceId={query.error instanceof ApiError ? query.error.traceId : undefined}
        retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
        onRetry={canRetry ? () => query.refetch() : undefined}
      />
    );
  } else if (query.isLoading) {
    body = <LoadingState />;
  } else {
    const series = query.data?.series;
    const candles = series?.kind === "ok" ? series.value.candles : [];
    const outcome = computeComparison(baseCandles, candles);
    if (outcome.kind === "empty") body = <EmptyState>겹치는 캔들이 없습니다.</EmptyState>;
    else if (outcome.kind === "scale_mismatch") body = <EmptyState>통화·스케일이 달라 스프레드를 계산할 수 없습니다.</EmptyState>;
    else
      body = (
        <>
          <div>
            <p className="text-xs text-fg-muted">정규화 오버레이 (base=100, 메인 페인 {MAIN_PANE_INDEX})</p>
            <Sparkline
              label={`${baseInstrumentId} vs ${symbolRef.instrumentId} 정규화 오버레이`}
              series={[
                { label: baseInstrumentId, color: CATEGORICAL_PALETTE[0]!, points: outcome.overlayBase },
                { label: symbolRef.instrumentId, color: CATEGORICAL_PALETTE[1]!, points: outcome.overlayOther },
              ]}
            />
          </div>
          <div>
            <p className="text-xs text-fg-muted">스프레드 (서브 페인 {spreadPaneEntry().paneIndex})</p>
            <Sparkline label={`${symbolRef.instrumentId} 스프레드`} series={[{ label: "spread", color: CATEGORICAL_PALETTE[2]!, points: outcome.spreadPoints }]} />
          </div>
        </>
      );
  }

  return (
    <section aria-label={`비교 심볼 ${symbolRef.instrumentId}`} data-testid={`compare-pane-${symbolRef.instrumentId}`} className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-fg">
          {symbolRef.instrumentId} <span className="text-xs text-fg-muted">({symbolRef.venue})</span>
        </h3>
        <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
          제거
        </Button>
      </div>
      {body}
    </section>
  );
}

export function CompareSymbols({
  baseVenue, baseInstrumentId, baseCandles, timeframe, start, end,
  compareSymbols, onAdd, onRemove, fetchCandles, listInstruments,
}: CompareSymbolsProps) {
  return (
    <section aria-label="비교 심볼 목록" className="space-y-3">
      <h2 className="text-sm font-medium text-fg-secondary">비교 심볼 ({compareSymbols.length})</h2>
      <AddSymbolForm baseVenue={baseVenue} baseInstrumentId={baseInstrumentId} existing={compareSymbols} listInstruments={listInstruments} onAdd={onAdd} />
      <div className="space-y-3">
        {compareSymbols.map((symbolRef) => (
          <CompareSymbolPane
            key={`${symbolRef.venue}:${symbolRef.instrumentId}`}
            symbolRef={symbolRef}
            baseInstrumentId={baseInstrumentId}
            baseCandles={baseCandles}
            timeframe={timeframe}
            start={start}
            end={end}
            fetchCandles={fetchCandles}
            onRemove={() => onRemove(symbolRef)}
          />
        ))}
      </div>
    </section>
  );
}
