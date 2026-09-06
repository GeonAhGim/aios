import { ApiError } from "@aios/api-client";
import { useExecutions } from "@aios/shared-hooks";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import type { CandlestickPoint } from "@aios/ui-web";
import { Button, EmptyState, LoadingState, Select, StatusBadge } from "@aios/ui-web";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { NotFoundState } from "../../components/NotFoundState";
import { useCursorPage } from "../../hooks/useCursorPage";
import { usePositionJournal, usePositionList, usePositionsClient, type PositionsClientLike } from "../../hooks/usePositions";
import type { CursorNavigatorMeta } from "../../lib/cursorPagination";

// CH-10 — chart-engine 엔진 내부는 손대지 않는다: createTimeScale/createPriceScale
// (공개 API)로 캔들 시간·가격 범위를 이 컴포넌트 소유 DOM 오버레이에 그대로
// 재사용할 뿐이다(마커 프리미티브가 아직 없다, task-1569 note). execution_id ↔
// position_key를 잇는 서버 라우트는 없어(ExecutionCardResponse에 instrument_id가
// 없다) 새 라우트를 만들지 않고(decision), 실행 선택은 범례 배지·게이트 용도로만
// 쓴다 — 마커는 차트의 instrumentId로 positions.list를 필터링해 얻은
// position_key의 journal에서 온다. 저널 항목(raw)은 파서가 없어(task-1524
// decision, 새 파서 금지) PositionJournalPanel.cellText 관용으로 읽고, 방향은
// entry_type=FILL일 때 qty_delta 부호로만 가른다(snapshot_builder.py 서버 규칙을
// 그대로 옮긴 것 — 새 분류기 아님).

export interface StrategyMarkersProps {
  instrumentId: string;
  points: readonly CandlestickPoint[];
  client?: PositionsClientLike;
  height?: number;
}

type MarkerDirection = "buy" | "sell" | "funding" | "fee" | "other";

interface JournalMarker {
  key: string;
  time: number;
  price: number;
  direction: MarkerDirection;
  qtyLabel: string;
  priceLabel: string;
}

interface CommittedPage {
  cursor: string | undefined;
  nextCursor: string | null;
}

const DEFAULT_HEIGHT = 160;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isMoney(value: unknown): value is { amount: string; currency?: unknown } {
  return isRecord(value) && typeof value.amount === "string";
}

const DIRECTION_META: Record<MarkerDirection, { label: string; glyph: string }> = {
  buy: { label: "매수", glyph: "▲" },
  sell: { label: "매도", glyph: "▼" },
  funding: { label: "펀딩비 정산", glyph: "◆" },
  fee: { label: "수수료", glyph: "•" },
  other: { label: "기타 조정", glyph: "○" },
};

// entry_type=FILL의 방향(매수/매도)은 qty_delta 부호로 정해진다 — 서버
// snapshot_builder.py의 규칙을 그대로 옮긴 것(§ 파일 상단 주석 참고).
function toMarker(entry: unknown): JournalMarker | null {
  if (!isRecord(entry)) return null;
  const occurredAt = entry.occurred_at;
  const entryType = entry.entry_type;
  if (typeof occurredAt !== "string" || typeof entryType !== "string") return null;
  const timeMs = Date.parse(occurredAt);
  if (!Number.isFinite(timeMs)) return null;

  const qtyDeltaRaw = typeof entry.qty_delta === "string" ? entry.qty_delta : "0";
  const isNegative = qtyDeltaRaw.trim().startsWith("-");
  const qtyLabel = isNegative ? qtyDeltaRaw.trim().slice(1) : qtyDeltaRaw.trim();

  let direction: MarkerDirection = "other";
  if (entryType === "FILL") {
    direction = qtyDeltaRaw === "0" ? "other" : isNegative ? "sell" : "buy";
  } else if (entryType === "FUNDING") {
    direction = "funding";
  } else if (entryType === "FEE") {
    direction = "fee";
  }

  const priceMoney = entry.price;
  const priceLabel = isMoney(priceMoney) ? priceMoney.amount : "-";
  const price = isMoney(priceMoney) ? Number(priceMoney.amount) : NaN;

  const sequenceNo = entry.sequence_no;
  const key = typeof sequenceNo === "number" ? String(sequenceNo) : `${occurredAt}-${entryType}`;

  return { key, time: timeMs / 1000, price, direction, qtyLabel, priceLabel };
}

function markerAriaLabel(marker: JournalMarker): string {
  const { label } = DIRECTION_META[marker.direction];
  if (marker.direction === "buy" || marker.direction === "sell") {
    return `${label} ${marker.qtyLabel} @ ${marker.priceLabel}`;
  }
  return label;
}

// PositionsQueryError(routes/portfolio)와 동일한 3갈래 dispatch(§3.3) — 새 분류기를
// 만들지 않고 routeApiError/classifyForbidden만 경유한다. 화면마다 로컬로 두는 것이
// 기존 관용이다(ExecutionsListError/RebalanceError와 동일 패턴).
function JournalQueryError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const routed = routeApiError(error);
  if (routed.kind === "not_found") {
    return <NotFoundState title="이 포지션의 저널을 찾을 수 없습니다." />;
  }
  if (classifyForbidden(error)) {
    return <ForbiddenNotice error={error} />;
  }
  const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

export function StrategyMarkers({ instrumentId, points, client: injected, height = DEFAULT_HEIGHT }: StrategyMarkersProps) {
  const client = usePositionsClient(injected);
  const executionsQuery = useExecutions();
  const executions = executionsQuery.data ?? [];
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null);
  const selectedExecution = executions.find((e) => e.executionId === selectedExecutionId) ?? null;

  const positionsQuery = usePositionList(client, { instrumentId }, { enabled: selectedExecutionId !== null });
  const matchedPosition = (positionsQuery.data?.items ?? []).find((item) => item.kind === "ok");
  const positionKey = matchedPosition?.kind === "ok" ? matchedPosition.value.position_key : null;

  const [committed, setCommitted] = useState<CommittedPage | null>(null);
  const meta: CursorNavigatorMeta | null = committed ? { next_cursor: committed.nextCursor } : null;
  const pager = useCursorPage(meta);
  const journalEnabled = selectedExecutionId !== null && positionKey !== null;
  const journalQuery = usePositionJournal(client, positionKey ?? "", {
    cursor: pager.cursor,
    limit: 200,
    enabled: journalEnabled,
  });

  if (
    journalQuery.data &&
    (!committed || committed.cursor !== pager.cursor || committed.nextCursor !== journalQuery.data.nextCursor)
  ) {
    setCommitted({ cursor: pager.cursor, nextCursor: journalQuery.data.nextCursor });
  }

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const timeDomain = useMemo(() => {
    if (points.length === 0) return null;
    const times = points.map((p) => p.time);
    const from = Math.min(...times);
    const to = Math.max(...times);
    return from < to ? { from, to } : null;
  }, [points]);

  const priceDomain = useMemo(() => {
    if (points.length === 0) return null;
    const prices = points.flatMap((p) => [p.high, p.low]);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    return min < max ? { min, max } : null;
  }, [points]);

  const markers = useMemo(() => {
    if (!timeDomain) return [];
    const items = journalQuery.data?.items ?? [];
    return items
      .map(toMarker)
      .filter((m): m is JournalMarker => m !== null && m.time >= timeDomain.from && m.time <= timeDomain.to);
  }, [journalQuery.data, timeDomain]);

  const timeScale = useMemo(
    () => (timeDomain ? createTimeScale({ range: timeDomain, width }) : null),
    [timeDomain, width],
  );
  const priceScale = useMemo(
    () => (priceDomain ? createPriceScale({ range: priceDomain, height }) : null),
    [priceDomain, height],
  );

  return (
    <section aria-label="전략 신호·체결 마커" className="space-y-2">
      <div className="flex items-center gap-3">
        <Select
          value={selectedExecutionId !== null ? String(selectedExecutionId) : ""}
          onChange={(e) => setSelectedExecutionId(e.target.value === "" ? null : Number(e.target.value))}
          data-testid="strategy-markers-execution-select"
        >
          <option value="">실행 선택</option>
          {executions.map((exec) => (
            <option key={exec.executionId} value={exec.executionId}>
              {exec.strategyId} (#{exec.executionId})
            </option>
          ))}
        </Select>
        {selectedExecution && <StatusBadge status={selectedExecution.status} />}
      </div>

      {selectedExecutionId === null ? (
        <EmptyState>실행을 선택하면 신호·체결 마커를 표시합니다.</EmptyState>
      ) : positionsQuery.isPending ? (
        <LoadingState />
      ) : positionKey === null ? (
        <EmptyState>이 심볼에 대한 포지션이 없습니다.</EmptyState>
      ) : journalQuery.isError ? (
        <JournalQueryError error={journalQuery.error} onRetry={() => journalQuery.refetch()} />
      ) : journalQuery.isPending ? (
        <LoadingState />
      ) : markers.length === 0 ? (
        <EmptyState>표시할 체결·신호가 없습니다.</EmptyState>
      ) : (
        <div
          ref={containerRef}
          className="relative w-full overflow-hidden rounded-md border border-border bg-surface"
          style={{ height }}
          data-testid="strategy-markers-overlay"
        >
          {markers.map((marker) => (
            <span
              key={marker.key}
              role="img"
              aria-label={markerAriaLabel(marker)}
              title={markerAriaLabel(marker)}
              className="absolute -translate-x-1/2 -translate-y-1/2 text-sm"
              style={{
                left: `${timeScale?.timeToX(marker.time) ?? 0}px`,
                top: `${priceScale && Number.isFinite(marker.price) ? priceScale.priceToY(marker.price) : height / 2}px`,
              }}
            >
              {DIRECTION_META[marker.direction].glyph}
            </span>
          ))}
        </div>
      )}

      {journalEnabled && (
        <div className="flex gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={pager.prev} disabled={!pager.hasPrev} data-testid="strategy-markers-prev">
            이전
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={pager.next} disabled={!pager.hasNext} data-testid="strategy-markers-next">
            다음
          </Button>
        </div>
      )}
    </section>
  );
}
