import { useReconciliationStates, useResolveReconciliation } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  RESOLVABLE_RECONCILIATION_STATUSES,
} from "@aios/shared-types";
import type { ReconciliationStateView } from "@aios/shared-types";
import { Button, EmptyState, LoadingState, PageHeader, StatusBadge, Textarea } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// task-2337(FE-OPS-3): spec §3.3 에러 taxonomy — 목록 조회·해소(resolve) 실패는
// err.message를 직접 노출하지 않고 routeApiError로 판정해 400/403/그 외를
// BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다(MandateActionError와
// 동일 3-way 패턴). resolve_reconciliation.py:69-72의 NotResolvableError(409)는
// 이 화면이 재구현하지 않는다 — RESOLVABLE_RECONCILIATION_STATUSES로 버튼 노출
// 여부만 미리 판단하고, 그래도 409가 나면 ErrorMessage가 그대로 보여준다(decision:
// 판정은 서버 권위).
function ReconciliationActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
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

export function ReconciliationPage() {
  const { data, isLoading, isError, error, refetch } = useReconciliationStates();
  const resolve = useResolveReconciliation();

  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<{ targetRef: string; error: unknown } | null>(null);

  function reasonFor(targetRef: string) {
    return reasons[targetRef] ?? "";
  }

  function handleResolve(targetRef: string) {
    const reason = reasonFor(targetRef).trim();
    if (!reason) {
      setActionError({ targetRef, error: new Error("해소 사유를 입력하세요.") });
      return;
    }
    setActionError(null);
    resolve.mutate(
      { targetRef, body: { reason } },
      { onError: (err) => setActionError({ targetRef, error: err }) },
    );
  }

  function renderState(state: ReconciliationStateView) {
    const resolvable = RESOLVABLE_RECONCILIATION_STATUSES.has(state.aggregateStatus);
    return (
      <li key={state.targetRef} className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center gap-2">
          <p className="font-medium text-fg">
            {state.targetType} · {state.targetRef}
          </p>
          <StatusBadge status={state.aggregateStatus} />
        </div>
        {state.blockingReason && <p className="text-sm text-fg-muted">{state.blockingReason}</p>}
        <p className="text-xs text-fg-muted">
          최근 확인: {new Date(state.lastCheckedAt).toLocaleString()}
          {state.lastHealthyAt && ` · 최근 정상: ${new Date(state.lastHealthyAt).toLocaleString()}`}
          {" · revision "}
          {state.revision}
        </p>
        {resolvable && (
          <div className="mt-3 flex items-end gap-2">
            <Textarea
              placeholder="해소 사유"
              rows={2}
              value={reasonFor(state.targetRef)}
              onChange={(e) => setReasons((prev) => ({ ...prev, [state.targetRef]: e.target.value }))}
              className="w-72"
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={resolve.isPending}
              onClick={() => handleResolve(state.targetRef)}
            >
              불일치 해소
            </Button>
          </div>
        )}
        {actionError?.targetRef === state.targetRef && (
          <div className="mt-3">
            <ReconciliationActionError error={actionError.error} />
          </div>
        )}
      </li>
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="대사(Reconciliation) 불일치 해소" />
        {isError ? (
          <ReconciliationActionError error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <LoadingState />
        ) : data && data.states.length > 0 ? (
          <ul className="space-y-3">{data.states.map(renderState)}</ul>
        ) : (
          <EmptyState>대사 상태가 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
