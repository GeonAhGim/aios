import type { ApiResponsePageMeta } from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import { useAuditLog } from "@aios/shared-hooks";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { Pagination } from "../../components/Pagination";
import { derivePageState } from "../../lib/pagination";
import { useTranslation } from "react-i18next";

const DEFAULT_PAGE_SIZE = 20;

// admin.py:84 GET /admin/audit-log는 PLT-35-fix(task-3850)의
// require_break_glass("tenant_read")를 소비한다 -- 그랜트 id가 없으면 서버가 호출
// 자체를 거부하므로(useAuditLog(task-4025)가 enabled 가드) 그랜트를 아직 입력하지
// 않은 상태(!submittedGrantId)와 조회했지만 결과가 비어 있는 상태를 구분해 보여준다.
// admin_deps.py::require_break_glass가 스코프 불일치·만료 등 유효하지 않은 그랜트를
// BreakGlassInvalidStateError -> STATE_INVALID_TRANSITION(409)로 매핑한다(fail-closed,
// exception_registry.py) -- err.message를 직접 노출하지 않고 routeApiError/
// classifyForbidden으로 판정해 403/409/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(PayoutsPage.MarkPaidActionError와 같은 관용).
function AuditLogError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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

export function AuditLogPage() {
  const { t } = useTranslation();
  const [grantIdInput, setGrantIdInput] = useState("");
  const [submittedGrantId, setSubmittedGrantId] = useState("");
  const [actionType, setActionType] = useState("");
  const [targetType, setTargetType] = useState("");
  const [targetId, setTargetId] = useState("");
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, error, refetch } = useAuditLog(submittedGrantId, {
    actionType: actionType || undefined,
    targetType: targetType || undefined,
    targetId: targetId || undefined,
    page,
    pageSize: DEFAULT_PAGE_SIZE,
  });

  function handleSearch() {
    setPage(1);
    setSubmittedGrantId(grantIdInput.trim());
  }

  const routed = isError ? routeApiError(error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";

  const meta: ApiResponsePageMeta | null = data
    ? { total: data.total, page: data.page, size: data.pageSize, next_cursor: null }
    : null;
  const pageState = derivePageState(meta, { defaultSize: DEFAULT_PAGE_SIZE });

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("legacy.auditLogPage.title1")} />

        <div className="flex flex-wrap items-end gap-2">
          <Input
            aria-label={t("legacy.auditLogPage.label2")}
            placeholder={t("legacy.auditLogPage.placeholder2")}
            value={grantIdInput}
            onChange={(e) => setGrantIdInput(e.target.value)}
            className="w-72"
          />
          <Input
            aria-label={t("legacy.auditLogPage.label3")}
            placeholder={t("legacy.auditLogPage.placeholder3")}
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            className="w-40"
          />
          <Input
            aria-label={t("legacy.auditLogPage.label4")}
            placeholder={t("legacy.auditLogPage.placeholder4")}
            value={targetType}
            onChange={(e) => setTargetType(e.target.value)}
            className="w-40"
          />
          <Input
            aria-label={t("legacy.auditLogPage.label5")}
            placeholder={t("legacy.auditLogPage.placeholder5")}
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            className="w-40"
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={!grantIdInput.trim()}
            onClick={handleSearch}
          >
            {t("legacy.auditLogPage.t6")}
          </Button>
        </div>

        {!submittedGrantId ? (
          <EmptyState>{t("legacy.auditLogPage.t7")}</EmptyState>
        ) : isError ? (
          <AuditLogError error={error} onRetry={canRetry ? () => refetch() : undefined} />
        ) : isLoading ? (
          <LoadingState />
        ) : data && data.items.length > 0 ? (
          <ul className="space-y-2" data-testid="audit-log-list">
            {data.items.map((entry) => (
              <li key={entry.logId} className="rounded-md border border-border bg-surface p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-fg">{entry.actionType}</span>
                  <span className="text-xs text-fg-muted">{new Date(entry.createdAt).toLocaleString()}</span>
                </div>
                <p className="text-xs text-fg-muted">
                  {t("legacy.auditLogPage.t8", { actorAgent: entry.actorAgent, userId: entry.userId ?? "-" })}
                </p>
                {entry.targetType && (
                  <p className="text-xs text-fg-muted">
                    {t("legacy.auditLogPage.t9", {
                      targetType: entry.targetType,
                      targetId: entry.targetId ?? "-",
                    })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>{t("legacy.auditLogPage.t10")}</EmptyState>
        )}

        {submittedGrantId && !isError && data && (
          <Pagination state={pageState} onPageChange={setPage} />
        )}
      </div>
    </AppShell>
  );
}
