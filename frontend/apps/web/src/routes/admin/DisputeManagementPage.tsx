import { useAdminDisputes, useResolveDispute } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, classifyServerError, routeApiError } from "@aios/shared-types";
import type { DisputeResolveRequest } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useRef, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 분쟁 처리(resolve) 실패는 err.message를 직접 노출하지
// 않고 routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-901/910/911 패턴). 지금까지 이 화면은 resolve.mutate를
// 콜백 없이 호출해 실패를 완전히 조용히 삼켰다 — 에러 상태 자체가 없었다.
function ResolveDisputeError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
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

export function DisputeManagementPage() {
  const { data: disputes, isLoading } = useAdminDisputes();
  const resolve = useResolveDispute();
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [resolveError, setResolveError] = useState<{ disputeId: number; error: unknown } | null>(null);
  const lastAttempt = useRef<{ disputeId: number; body: DisputeResolveRequest } | null>(null);

  function submitResolve(disputeId: number, body: DisputeResolveRequest) {
    lastAttempt.current = { disputeId, body };
    setResolveError(null);
    resolve.mutate(
      { disputeId, body },
      { onError: (err) => setResolveError({ disputeId, error: err }) },
    );
  }

  function retryResolve() {
    if (!lastAttempt.current) return;
    submitResolve(lastAttempt.current.disputeId, lastAttempt.current.body);
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="분쟁 관리" />
        {isLoading ? (
          <LoadingState />
        ) : disputes && disputes.length > 0 ? (
          <ul className="space-y-3">
            {disputes.map((d) => (
              <li key={d.id} className="rounded-lg border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-fg">
                        분쟁 #{d.id} · 구매 #{d.purchaseId}
                      </p>
                      <StatusBadge status={d.status} />
                    </div>
                    <p className="text-sm text-fg-muted">{d.reason}</p>
                    <p className="text-xs text-fg-muted">
                      {new Date(d.createdAt).toLocaleString()}
                    </p>
                  </div>
                  {d.status === "OPEN" && (
                    <div className="flex items-center gap-2">
                      <Input
                        type="text"
                        placeholder="처리 사유"
                        value={reasons[d.id] ?? ""}
                        onChange={(e) => setReasons((r) => ({ ...r, [d.id]: e.target.value }))}
                        className="w-40"
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() =>
                          submitResolve(d.id, {
                            decision: "NORMAL_RISK_REALIZATION",
                            reason: reasons[d.id] || "",
                          })
                        }
                      >
                        정상 리스크 실현(기각)
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() =>
                          submitResolve(d.id, {
                            decision: "DELISTED_AND_REFUND",
                            reason: reasons[d.id] || "",
                          })
                        }
                      >
                        상장폐지+환불
                      </Button>
                    </div>
                  )}
                </div>
                {resolveError?.disputeId === d.id && (
                  <div className="mt-3">
                    <ResolveDisputeError error={resolveError.error} onRetry={retryResolve} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>분쟁 건이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
