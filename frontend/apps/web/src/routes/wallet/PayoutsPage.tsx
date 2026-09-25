import type { ApiResponseMeta, ApiResponsePageMeta } from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import type { MarkPayoutPaidResult } from "@aios/shared-types";
import { classifyForbidden, classifyServerError, parseHoldView, parsePayoutBatchView, routeApiError } from "@aios/shared-types";
import { apiClient } from "@aios/shared-hooks";
import { Alert, Button, Card, EmptyState, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { HoldStatusBadge, PayoutBatchStatusBadge } from "../../components/HoldStatusBadge";
import { useCursorPage } from "../../hooks/useCursorPage";
import { useTranslation } from "react-i18next";

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

// task-4026(FE-OPS-7c): admin.ts::markPayoutPaid(task-4024)는 이미 실제 서버
// 라우트(POST /admin/ledger/payouts/{batch_id}/paid)에 배선돼 있다 — 위 두
// 목록 GET과 달리 이 액션은 reject 기본값이 아니라 apiClient를 직접 호출한다.
// break-glass 그랜트 발급/승인 화면이 아직 없어(admin.ts 주석 참고) 호출자가
// 이미 들고 있는 grant id를 이 화면에서 직접 입력받아 그대로 흘려보낸다.
export type MarkPayoutPaidFn = (
  batchId: string,
  externalRef: string,
  breakGlassGrantId: string,
) => Promise<MarkPayoutPaidResult>;

const markPayoutPaidDefault: MarkPayoutPaidFn = (batchId, externalRef, breakGlassGrantId) =>
  apiClient.markPayoutPaid(batchId, externalRef, breakGlassGrantId);

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
  const { t } = useTranslation();
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
            {t("legacy.payoutsPage.t1", { accountcode: hold.account_code, expiresat: hold.expires_at })}
          </p>
        </div>
        <HoldStatusBadge hold={parsed} />
      </div>
      <p className="tabular mt-2 text-right text-lg">{hold.amount}</p>
    </Card>
  );
}

// spec §3.3 에러 taxonomy: 정산 배치 확정(markPayoutPaid) 실패는 err.message를
// 직접 노출하지 않고 routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/
// ErrorMessage 경로로만 보여준다(WalletTopupsPage.TopupActionError·
// DisputeManagementPage.ResolveDisputeError와 같은 관용).
function MarkPaidActionError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}

function PayoutBatchCard({ raw, markPayoutPaid }: { raw: unknown; markPayoutPaid: MarkPayoutPaidFn }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [externalRef, setExternalRef] = useState("");
  const [grantId, setGrantId] = useState("");
  const parsed = parsePayoutBatchView(raw);
  const batchId = parsed.kind === "ok" ? parsed.value.batch_id : null;

  const markPaid = useMutation({
    mutationFn: () => {
      if (!batchId) return Promise.reject(new Error("batch_id가 없습니다."));
      return markPayoutPaid(batchId, externalRef, grantId);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["payoutBatches"] }),
  });

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
            {t("legacy.payoutsPage.t2", { length: batch.capture_entry_ids.length })}{batch.release_entry_id && <> {t("legacy.payoutsPage.t3", { releaseentryid: batch.release_entry_id })}</>}
            {batch.paid_entry_id && <> {t("legacy.payoutsPage.t4", { paidentryid: batch.paid_entry_id })}</>}
          </p>
        </div>
        <PayoutBatchStatusBadge payoutBatch={parsed} />
      </div>
      <p className="tabular mt-2 text-right text-lg">{batch.amount}</p>

      {batch.state === "RELEASED" && !markPaid.isSuccess && (
        <div className="mt-3 space-y-2 border-t border-border pt-3" data-testid="payout-batch-mark-paid">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label={t("legacy.payoutsPage.label9")}
              placeholder={t("legacy.payoutsPage.placeholder9")}
              value={externalRef}
              onChange={(e) => setExternalRef(e.target.value)}
              className="flex-1"
            />
            <Input
              aria-label={t("legacy.payoutsPage.label10")}
              placeholder={t("legacy.payoutsPage.placeholder10")}
              value={grantId}
              onChange={(e) => setGrantId(e.target.value)}
              className="flex-1"
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={markPaid.isPending}
              disabled={!externalRef || !grantId}
              onClick={() => markPaid.mutate()}
            >
              {t("legacy.payoutsPage.t11")}
            </Button>
          </div>
          {markPaid.isError && <MarkPaidActionError error={markPaid.error} onRetry={() => markPaid.mutate()} />}
        </div>
      )}
      {markPaid.isSuccess && (
        <div className="mt-3 border-t border-border pt-3">
          <Alert tone="success">{t("legacy.payoutsPage.t12")}</Alert>
        </div>
      )}
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
  const { t } = useTranslation();
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
            {t("legacy.payoutsPage.t5")}</Button>
          <Button type="button" variant="secondary" size="sm" disabled={!cursorPage.hasNext} onClick={cursorPage.next}>
            {t("legacy.payoutsPage.t6")}</Button>
        </div>
      )}
    </section>
  );
}

interface PayoutsPageProps {
  fetchHolds?: FetchHoldsPage;
  fetchPayoutBatches?: FetchPayoutBatchesPage;
  markPayoutPaid?: MarkPayoutPaidFn;
  staleAfterSec?: number;
  now?: Date;
}

export function PayoutsPage({
  fetchHolds = fetchHoldsPageDefault,
  fetchPayoutBatches = fetchPayoutBatchesPageDefault,
  markPayoutPaid = markPayoutPaidDefault,
  staleAfterSec,
  now,
}: PayoutsPageProps) {
  const { t } = useTranslation();
  return (
    <AppShell>
      <div className="max-w-3xl space-y-8">
        <CursorListSection
          title={t("legacy.payoutsPage.title7")}
          testId="holds"
          fetchPage={fetchHolds}
          renderItem={(raw, index) => <HoldCard key={index} raw={raw} />}
          emptyText="보류 중인 홀드가 없습니다."
          staleAfterSec={staleAfterSec}
          now={now}
        />
        <CursorListSection
          title={t("legacy.payoutsPage.title8")}
          testId="payoutBatches"
          fetchPage={fetchPayoutBatches}
          renderItem={(raw, index) => <PayoutBatchCard key={index} raw={raw} markPayoutPaid={markPayoutPaid} />}
          emptyText="정산 배치가 없습니다."
          staleAfterSec={staleAfterSec}
          now={now}
        />
      </div>
    </AppShell>
  );
}
