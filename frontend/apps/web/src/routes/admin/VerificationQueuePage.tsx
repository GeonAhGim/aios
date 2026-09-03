import { useVerificationQueue, useVerifyListing } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import type { VerificationDecisionRequest } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 검수 판정(verify) 실패는 err.message를 직접 노출하지
// 않고 routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-483/1072 패턴). 지금까지 verify.mutate가 콜백 없이
// 호출돼 실패를 완전히 조용히 삼켰다 — 에러 상태 자체가 없었다.
function VerifyActionError({ error }: { error: unknown }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function VerificationQueuePage() {
  const { data: queue, isLoading } = useVerificationQueue();
  const verify = useVerifyListing();
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});
  const [actionError, setActionError] = useState<{ listingId: number; error: unknown } | null>(
    null,
  );

  function submitVerify(listingId: number, body: VerificationDecisionRequest) {
    setActionError(null);
    verify.mutate(
      { listingId, body },
      { onError: (err) => setActionError({ listingId, error: err }) },
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="전략 검수 대기열" />
        {isLoading ? (
          <LoadingState />
        ) : queue && queue.length > 0 ? (
          <ul className="space-y-3">
            {queue.map((item) => (
              <li
                key={item.listingId}
                className="rounded-lg border border-border bg-surface p-4"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-fg">
                      {item.strategyId}@{item.strategyVersion}
                    </p>
                    <p className="tabular text-sm text-fg-muted">
                      가격 {item.price ?? "미정"} · 제출일{" "}
                      {new Date(item.submittedAt).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Input
                      type="text"
                      placeholder="반려 사유"
                      value={rejectReasons[item.listingId] ?? ""}
                      onChange={(e) =>
                        setRejectReasons((r) => ({ ...r, [item.listingId]: e.target.value }))
                      }
                      className="w-40"
                    />
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      onClick={() =>
                        submitVerify(item.listingId, {
                          decision: "REJECT",
                          rejectionReason: rejectReasons[item.listingId] || "사유 미기재",
                        })
                      }
                    >
                      반려
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="!bg-success hover:!bg-success/90"
                      onClick={() => submitVerify(item.listingId, { decision: "APPROVE" })}
                    >
                      승인
                    </Button>
                  </div>
                </div>
                {actionError?.listingId === item.listingId && (
                  <div className="mt-3">
                    <VerifyActionError error={actionError.error} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>대기 중인 검수 건이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
