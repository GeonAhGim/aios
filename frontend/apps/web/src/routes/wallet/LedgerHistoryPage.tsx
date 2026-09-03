import type { ApiResponseMeta, ApiResponsePageMeta } from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { Button, PageHeader } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { LedgerEntryList } from "../../components/LedgerEntryList";
import { useCursorPage } from "../../hooks/useCursorPage";

// spec §3.3 (C) JournalEntryView 목록 GET 라우트. task-657(LedgerEntryList·
// ledgerView.ts)·task-462(useCursorPage)·task-353(DataFreshness)를 처음으로 한
// 화면에 배선한다 — 세 조각 모두 재사용만 하고 새 파서·컴포넌트는 만들지 않는다.
export interface LedgerHistoryPageResult {
  entries: unknown[];
  meta: ApiResponseMeta;
}

export type FetchLedgerHistoryPage = (cursor: string | undefined) => Promise<LedgerHistoryPageResult>;

// 이 목록 GET 라우트는 서버에 아직 없다(task-628과 동일 decision — 원장 SSOT는
// 서버이므로 클라이언트가 항목·잔액을 임의로 채우지 않는다). 실제 엔드포인트가
// 생기면 이 기본 구현만 실제 apiClient 호출로 교체하면 되고, 그 전까지는 이
// 사실을 감추지 않고 표준 에러 경로(routeApiError+ErrorMessage) 그대로 보여준다.
const fetchLedgerHistoryPageDefault: FetchLedgerHistoryPage = () =>
  Promise.reject(new Error("원장 내역 조회 API가 아직 제공되지 않습니다."));

interface LedgerHistoryPageProps {
  fetchPage?: FetchLedgerHistoryPage;
  staleAfterSec?: number;
  now?: Date;
}

export function LedgerHistoryPage({ fetchPage = fetchLedgerHistoryPageDefault, staleAfterSec, now }: LedgerHistoryPageProps) {
  // useCursorPage는 "가장 최근에 받은 목록 조회 응답의 page meta"를 매 렌더마다
  // 그대로 받는 계약이다(useCursorPage.ts 주석) — cursor 자체는 이 값과 무관하게
  // navigator 내부 이력으로만 정해지므로, 직전 성공 응답의 meta를 그 cursor와 함께
  // state로 들고 있다가 넘긴다(같은 hook을 두 번 호출해 navigator를 두 개 만들지
  // 않는다). effect가 아니라 렌더 중 상태 조정(React 공식 패턴)으로 갱신해
  // 불필요한 커밋 왕복을 피한다.
  const [committed, setCommitted] = useState<{ cursor: string | undefined; meta: ApiResponsePageMeta | null } | null>(
    null,
  );
  const cursorPage = useCursorPage(committed?.meta ?? null);

  const query = useQuery({
    queryKey: ["ledgerHistoryPage", cursorPage.cursor],
    queryFn: () => fetchPage(cursorPage.cursor),
  });

  // committed는 처음엔 null이고, 첫 페이지의 cursor도 undefined이므로
  // "committed?.cursor !== cursorPage.cursor"만으로는 첫 커밋을 구분하지 못한다
  // (둘 다 undefined로 같아 보임) — committed 자체의 존재 여부를 먼저 본다.
  if (query.data && (!committed || committed.cursor !== cursorPage.cursor)) {
    setCommitted({ cursor: cursorPage.cursor, meta: query.data.meta.page });
  }

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";

  return (
    <AppShell>
      <div className="max-w-3xl space-y-4">
        <PageHeader
          title="원장 내역"
          action={query.data && <DataFreshness asOf={query.data.meta.as_of} staleAfterSec={staleAfterSec} now={now} />}
        />

        {query.isError ? (
          <ErrorMessage
            errorCode={query.error instanceof ApiError ? query.error.errorCode : undefined}
            message={query.error instanceof Error ? query.error.message : undefined}
            traceId={query.error instanceof ApiError ? query.error.traceId : undefined}
            retryAfterSec={routed?.kind === "backoff_retry" ? routed.afterSec : undefined}
            onRetry={canRetry ? () => query.refetch() : undefined}
          />
        ) : (
          <LedgerEntryList entries={query.data?.entries ?? []} isLoading={query.isLoading} />
        )}

        {!query.isError && (query.data || cursorPage.hasPrev) && (
          <div className="flex items-center justify-center gap-2">
            <Button type="button" variant="secondary" size="sm" disabled={!cursorPage.hasPrev} onClick={cursorPage.prev}>
              이전
            </Button>
            <Button type="button" variant="secondary" size="sm" disabled={!cursorPage.hasNext} onClick={cursorPage.next}>
              다음
            </Button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
