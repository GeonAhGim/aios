import type { ApiResponseMeta, ApiResponsePageMeta } from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import { parseHoldView, parsePayoutBatchView, routeApiError } from "@aios/shared-types";
import { Alert, Button, Card, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { HoldStatusBadge, PayoutBatchStatusBadge } from "../../components/HoldStatusBadge";
import { useCursorPage } from "../../hooks/useCursorPage";

// spec §3.3 (C) HoldView/PayoutBatchView 목록 화면. task-658(holdPayoutView.ts:
// parseHoldView/parsePayoutBatchView, HoldStatusBadge/PayoutBatchStatusBadge)을
// 처음으로 실제 라우트에 배선한다 — 새 파서·새 배지는 만들지 않고 재사용만 한다.
// LedgerHistoryPage(task-628 계열)와 같은 관용으로 목록 GET 라우트 두 개(홀드·정산
// 배치) 모두 서버에 아직 없다(task-709 선례) — 기본 fetch 구현은 표준 에러 경로
// (routeApiError+ErrorMessage)만 태우고, 실제 엔드포인트가 생기면 이 기본 구현만
// apiClient 호출로 교체하면 된다.
//
// §4.5 홀드/정산 배치 상태 전이는 서버(ledger_hold/payouts.py) 소관이다 — 이
// 화면은 서버가 내려준 state를 그대로 표시할 뿐 추론·보정하지 않는다. 금액도
// 문자열 Decimal 그대로 렌더링한다(Number 변환 금지 — 정밀도 손실은 결함).
export interface PayoutsPageListResult {
  items: unknown[];
  meta: ApiResponseMeta;
}

export type FetchHoldsPage = (cursor: string | undefined) => Promise<PayoutsPageListResult>;
export type FetchPayoutBatchesPage = (cursor: string | undefined) => Promise<PayoutsPageListResult>;

const fetchHoldsPageDefault: FetchHoldsPage = () =>
  Promise.reject(new Error("홀드 목록 조회 API가 아직 제공되지 않습니다."));

const fetchPayoutBatchesPageDefault: FetchPayoutBatchesPage = () =>
  Promise.reject(new Error("정산 배치 목록 조회 API가 아직 제공되지 않습니다."));

function ParseFailureCard({ kind, received, message }: { kind: "unsupported_schema_version" | "invalid"; received?: unknown; message: string }) {
  return (
    <Card data-testid="payouts-parse-error">
      <Alert tone="danger">
        {kind === "unsupported_schema_version" ? `지원하지 않는 schema_version입니다 (${String(received)}).` : message}
      </Alert>
    </Card>
  );
}

function HoldCard({ raw }: { raw: unknown }) {
  const parsed = parseHoldView(raw);
  if (parsed.kind !== "ok") {
    return <ParseFailureCard kind={parsed.kind} received={parsed.kind === "unsupported_schema_version" ? parsed.received : undefined} message="홀드 정보를 해석할 수 없습니다." />;
  }
  const hold = parsed.value;
  return (
    <Card data-testid="hold-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium text-fg">
            {hold.purpose} · {hold.reference}
          </p>
          <p className="text-xs text-fg-muted">
            {hold.account_code} · 만료 {hold.expires_at}
          </p>
        </div>
        <HoldStatusBadge hold={parsed} />
      </div>
      <p className="tabular mt-2 text-right text-lg">{hold.amount}</p>
    </Card>
  );
}

function PayoutBatchCard({ raw }: { raw: unknown }) {
  const parsed = parsePayoutBatchView(raw);
  if (parsed.kind !== "ok") {
    return <ParseFailureCard kind={parsed.kind} received={parsed.kind === "unsupported_schema_version" ? parsed.received : undefined} message="정산 배치 정보를 해석할 수 없습니다." />;
  }
  const batch = parsed.value;
  return (
    <Card data-testid="payout-batch-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium text-fg">
            {batch.seller_user_id} · {batch.period_start} ~ {batch.period_end}
          </p>
          <p className="text-xs text-fg-muted">
            캡처 {batch.capture_entry_ids.length}건
            {batch.release_entry_id && <> · 해제 {batch.release_entry_id}</>}
            {batch.paid_entry_id && <> · 지급 {batch.paid_entry_id}</>}
          </p>
        </div>
        <PayoutBatchStatusBadge payoutBatch={parsed} />
      </div>
      <p className="tabular mt-2 text-right text-lg">{batch.amount}</p>
    </Card>
  );
}

interface CursorListSectionProps {
  title: string;
  testId: string;
  fetchPage: (cursor: string | undefined) => Promise<PayoutsPageListResult>;
  renderItem: (raw: unknown, index: number) => ReactNode;
  emptyText: string;
  staleAfterSec?: number;
  now?: Date;
}

function CursorListSection({ title, testId, fetchPage, renderItem, emptyText, staleAfterSec, now }: CursorListSectionProps) {
  // LedgerHistoryPage와 같은 관용: useCursorPage는 매 렌더마다 "가장 최근 응답의
  // page meta"를 그대로 받는 계약이라 직전 성공 응답의 meta를 cursor와 함께
  // state로 들고 있다가 넘긴다. effect가 아닌 렌더 중 상태 조정으로 갱신한다.
  const [committed, setCommitted] = useState<{ cursor: string | undefined; meta: ApiResponsePageMeta | null } | null>(
    null,
  );
  const cursorPage = useCursorPage(committed?.meta ?? null);

  const query = useQuery({
    queryKey: [testId, cursorPage.cursor],
    queryFn: () => fetchPage(cursorPage.cursor),
  });

  if (query.data && (!committed || committed.cursor !== cursorPage.cursor)) {
    setCommitted({ cursor: cursorPage.cursor, meta: query.data.meta.page });
  }

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";
  const items = query.data?.items ?? [];

  return (
    <section className="space-y-3" data-testid={`${testId}-section`}>
      <PageHeader
        title={title}
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
      ) : query.isLoading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState>{emptyText}</EmptyState>
      ) : (
        <div className="space-y-3">{items.map(renderItem)}</div>
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
    </section>
  );
}

interface PayoutsPageProps {
  fetchHolds?: FetchHoldsPage;
  fetchPayoutBatches?: FetchPayoutBatchesPage;
  staleAfterSec?: number;
  now?: Date;
}

export function PayoutsPage({
  fetchHolds = fetchHoldsPageDefault,
  fetchPayoutBatches = fetchPayoutBatchesPageDefault,
  staleAfterSec,
  now,
}: PayoutsPageProps) {
  return (
    <AppShell>
      <div className="max-w-3xl space-y-8">
        <CursorListSection
          title="보류(홀드)"
          testId="holds"
          fetchPage={fetchHolds}
          renderItem={(raw, index) => <HoldCard key={index} raw={raw} />}
          emptyText="보류 중인 홀드가 없습니다."
          staleAfterSec={staleAfterSec}
          now={now}
        />
        <CursorListSection
          title="정산 배치"
          testId="payoutBatches"
          fetchPage={fetchPayoutBatches}
          renderItem={(raw, index) => <PayoutBatchCard key={index} raw={raw} />}
          emptyText="정산 배치가 없습니다."
          staleAfterSec={staleAfterSec}
          now={now}
        />
      </div>
    </AppShell>
  );
}
