import { useConfirmTopup, usePendingTopups } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 입금확인(confirmTopup) 실패는 err.message를 직접
// 노출하지 않고 routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/
// ErrorMessage 경로로만 보여준다(task-483/1072 패턴). 지금까지 confirm.mutate가
// 콜백 없이 호출돼 실패를 완전히 조용히 삼켰다 — 에러 상태 자체가 없었다.
// idempotencyKey는 매 클릭마다 새로 발급하는 기존 동작을 그대로 둔다 — 금전
// 라우트라 멱등 키 수명주기는 여기서 건드리지 않는다.
function TopupActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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

export function WalletTopupsPage() {
  const {
    data,
    isLoading,
    isError: topupsIsError,
    error: topupsError,
    refetch: refetchTopups,
  } = usePendingTopups();
  const confirm = useConfirmTopup();
  const [actionError, setActionError] = useState<{ topupId: number; error: unknown } | null>(
    null,
  );

  function handleConfirm(topupId: number) {
    setActionError(null);
    confirm.mutate(
      { topupId, idempotencyKey: crypto.randomUUID() },
      { onError: (err) => setActionError({ topupId, error: err }) },
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="충전 요청 대기 목록" />
        {topupsIsError ? (
          <TopupActionError error={topupsError} onRetry={() => refetchTopups()} />
        ) : isLoading ? (
          <LoadingState />
        ) : data && data.items.length > 0 ? (
          <ul className="space-y-3">
            {data.items.map((t) => (
              <li
                key={t.id}
                className="rounded-lg border border-border bg-surface p-4"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-fg">충전요청 #{t.id}</p>
                    <p className="tabular text-sm text-fg-muted">
                      {t.requestedAmount} 크레딧 · {new Date(t.requestedAt).toLocaleString()}
                    </p>
                  </div>
                  <Button
                    type="button"
                    className="!bg-success hover:!bg-success/90"
                    loading={confirm.isPending}
                    onClick={() => handleConfirm(t.id)}
                  >
                    입금 확인
                  </Button>
                </div>
                {actionError?.topupId === t.id && (
                  <div className="mt-3">
                    <TopupActionError error={actionError.error} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>대기 중인 충전 요청이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
