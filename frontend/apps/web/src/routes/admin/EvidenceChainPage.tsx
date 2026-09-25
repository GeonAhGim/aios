import { useAuditTimeline, useVerifyAuditChain } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorNavigatorMeta } from "../../lib/cursorPagination";
import { useTranslation } from "react-i18next";

// task-2337(FE-OPS-3): spec §3.3 에러 taxonomy — 이 화면의 모든 실패는
// SafetyControlsPage와 동일 2-way 패턴(403/그 외)이다. 400 갈래가 없는 이유:
// timeline은 쿼리뿐이라 검증 실패가 없고, chain:verify는 바디가 없다(evidence.py
// 원문 — tenant_id는 query, body 없음).
function EvidenceActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={onRetry}
    />
  );
}

interface CommittedPage {
  cursor: string | undefined;
  nextCursor: string | null;
}

// verify_audit_chain()이 던지는 ChainIntegrityError(409)는 이 화면이 재계산하지
// 않는다 — "체인이 깨졌다"는 판정 자체가 서버 권위다(decision: LC-3 hash_chain
// 재구현 금지). 성공 응답 {verified:true}만 여기서 렌더한다.
export function EvidenceChainPage() {
  const { t } = useTranslation();
  const [committed, setCommitted] = useState<CommittedPage | null>(null);
  const meta: CursorNavigatorMeta | null = committed ? { next_cursor: committed.nextCursor } : null;
  const pager = useCursorPage(meta);
  const timeline = useAuditTimeline({ cursor: pager.cursor, limit: 20 });

  if (
    timeline.data &&
    (!committed || committed.cursor !== pager.cursor || committed.nextCursor !== timeline.data.nextCursor)
  ) {
    setCommitted({ cursor: pager.cursor, nextCursor: timeline.data.nextCursor });
  }

  const [tenantId, setTenantId] = useState("");
  const verify = useVerifyAuditChain();

  function handleVerify() {
    verify.mutate(tenantId.trim() || undefined);
  }

  const items = timeline.data?.items ?? [];

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("legacy.evidenceChainPage.title1")} />

        <div className="rounded-lg border border-border bg-surface p-4">
          <p className="font-medium text-fg">{t("legacy.evidenceChainPage.t2")}</p>
          <p className="text-sm text-fg-muted">
            {t("legacy.evidenceChainPage.t3")}</p>
          <div className="mt-2 flex items-end gap-2">
            <Input
              type="text"
              placeholder={t("legacy.evidenceChainPage.placeholder4")}
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              className="w-72"
            />
            <Button type="button" variant="primary" size="sm" loading={verify.isPending} onClick={handleVerify}>
              {t("legacy.evidenceChainPage.t5")}</Button>
          </div>
          {verify.isSuccess && verify.data && (
            <p className="mt-2 text-sm text-fg">
              {t("legacy.evidenceChainPage.t6")}<StatusBadge status={verify.data.verified ? "SUCCESS" : "ERROR"} />
            </p>
          )}
          {verify.isError && (
            <div className="mt-2">
              <EvidenceActionError error={verify.error} />
            </div>
          )}
        </div>

        <div>
          <p className="mb-2 font-medium text-fg">{t("legacy.evidenceChainPage.t7")}</p>
          {timeline.isError ? (
            <EvidenceActionError error={timeline.error} onRetry={() => timeline.refetch()} />
          ) : timeline.isLoading ? (
            <LoadingState />
          ) : items.length > 0 ? (
            <ul className="space-y-2">
              {items.map((event) => (
                <li key={event.id} className="rounded-md border border-border bg-surface p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-fg">
                      {event.aggregateType} · {event.action}
                    </span>
                    <StatusBadge status={event.outcome} />
                  </div>
                  <p className="text-xs text-fg-muted">
                    seq {event.sequenceNo} · {new Date(event.occurredAt).toLocaleString()}
                    {event.tenantId && ` · tenant ${event.tenantId}`}
                  </p>
                  <p className="text-xs text-fg-muted">{t("legacy.evidenceChainPage.t8", { eventHash: event.eventHash })}</p>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>{t("legacy.evidenceChainPage.t9")}</EmptyState>
          )}

          <div className="mt-2 flex gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={pager.prev}
              disabled={!pager.hasPrev || timeline.isFetching}
            >
              {t("legacy.evidenceChainPage.t10")}</Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={pager.next}
              disabled={!pager.hasNext || timeline.isFetching}
            >
              {t("legacy.evidenceChainPage.t11")}</Button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
