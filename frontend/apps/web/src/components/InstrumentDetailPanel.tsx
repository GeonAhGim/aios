import { ApiError } from "@aios/api-client";
import {
  isResourceNotFound,
  routeApiError,
  type InstrumentView,
  type ParsedSymbolAlias,
  type SymbolAlias,
} from "@aios/shared-types";
import { Alert, Card, EmptyState, LoadingState } from "@aios/ui-web";
import type { UseQueryResult } from "@tanstack/react-query";
import { ErrorMessage } from "./ErrorMessage";
import { NotFoundState } from "./NotFoundState";

// InstrumentsPage(task-824)의 행 클릭 상세 패널. "생애주기 전이 이력"은 서버에
// 별도 이벤트 로그 테이블이 없다(LA-10 마이그레이션 목록에 md_instrument/
// md_symbol_alias/md_corporate_action/md_venue_calendar_day뿐 — lifecycle event
// log 없음). 그래서 새 조회 엔드포인트를 지어내지 않고, 이미 파싱된
// InstrumentView.listed_at/delisted_at과 SymbolAlias.valid_from만으로 타임라인을
// 구성한다 — 서버가 실제로 보관하는 필드만 재배열해서 보여준다.
interface LifecycleTimelineEntry {
  at: string;
  label: string;
}

function buildLifecycleTimeline(instrument: InstrumentView, aliases: SymbolAlias[]): LifecycleTimelineEntry[] {
  const entries: LifecycleTimelineEntry[] = [
    { at: instrument.listed_at, label: `상장(LIST) · ${instrument.venue_symbol}` },
  ];
  for (const alias of aliases) {
    if (Date.parse(alias.valid_from) > Date.parse(instrument.listed_at)) {
      entries.push({ at: alias.valid_from, label: `별칭 변경(RENAME) → ${alias.alias_symbol}` });
    }
  }
  if (instrument.delisted_at) {
    entries.push({ at: instrument.delisted_at, label: "상장폐지(DELIST)" });
  }
  return entries.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const routed = routeApiError(error);
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

interface InstrumentDetailPanelProps {
  instrument: InstrumentView;
  aliasQuery: UseQueryResult<ParsedSymbolAlias[]>;
}

export function InstrumentDetailPanel({ instrument, aliasQuery }: InstrumentDetailPanelProps) {
  const validAliases = (aliasQuery.data ?? []).filter((a) => a.kind === "ok").map((a) => a.value);
  const timeline = buildLifecycleTimeline(instrument, validAliases);

  return (
    <Card data-testid="instrument-detail">
      <p className="font-medium text-fg">
        {instrument.canonical_symbol} ({instrument.venue_symbol}) · {instrument.venue}
      </p>
      <p className="mt-1 text-xs text-fg-muted">
        틱 {instrument.tick_size} · 랏 {instrument.lot_size} · 상장 {instrument.listed_at}
        {instrument.delisted_at && <> · 상장폐지 {instrument.delisted_at}</>}
      </p>

      <div className="mt-4">
        <p className="text-sm font-medium text-fg">별칭</p>
        {aliasQuery.isLoading ? (
          <LoadingState />
        ) : aliasQuery.isError ? (
          isResourceNotFound(aliasQuery.error) ? (
            <NotFoundState
              title="별칭 정보를 찾을 수 없습니다"
              description="삭제되었거나 존재하지 않는 심볼입니다."
            />
          ) : (
            <ErrorBanner error={aliasQuery.error} onRetry={() => aliasQuery.refetch()} />
          )
        ) : (aliasQuery.data ?? []).length === 0 ? (
          <EmptyState>등록된 별칭이 없습니다.</EmptyState>
        ) : (
          <ul className="mt-1 space-y-1" data-testid="alias-list">
            {(aliasQuery.data ?? []).map((parsed, index) =>
              parsed.kind === "ok" ? (
                <li key={parsed.value.alias_id} className="text-xs text-fg-muted">
                  {parsed.value.alias_symbol} · {parsed.value.valid_from} ~ {parsed.value.valid_to ?? "현재"}
                </li>
              ) : (
                <li key={`invalid-${index}`}>
                  <Alert tone="danger">별칭 정보를 해석할 수 없습니다.</Alert>
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      {!aliasQuery.isError && (
        <div className="mt-4">
          <p className="text-sm font-medium text-fg">생애주기 전이 이력</p>
          <ul className="mt-1 space-y-1" data-testid="lifecycle-timeline">
            {timeline.map((entry, index) => (
              <li key={index} className="text-xs text-fg-muted">
                {entry.at} · {entry.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
