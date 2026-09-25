import { useVerificationQueue, useVerifyListing } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import type { VerificationDecisionRequest } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useTranslation } from "react-i18next";

// spec §3.3 에러 taxonomy: 검수 판정(verify) 실패는 err.message를 직접 노출하지
// 않고 routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-483/1072 패턴). 지금까지 verify.mutate가 콜백 없이
// 호출돼 실패를 완전히 조용히 삼켰다 — 에러 상태 자체가 없었다.
function VerifyActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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

export function VerificationQueuePage() {
  const { t } = useTranslation();
  const {
    data: queue,
    isLoading,
    isError: queueIsError,
    error: queueError,
    refetch: refetchQueue,
  } = useVerificationQueue();
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
        <PageHeader title={t("legacy.verificationQueuePage.title1")} />
        {queueIsError ? (
          <VerifyActionError error={queueError} onRetry={() => refetchQueue()} />
        ) : isLoading ? (
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
                      {t("legacy.verificationQueuePage.t2")}{item.price ?? "미정"} {t("legacy.verificationQueuePage.t3", { val: " " })}
                      {new Date(item.submittedAt).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Input
                      type="text"
                      placeholder={t("legacy.verificationQueuePage.placeholder4")}
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
                      {t("legacy.verificationQueuePage.t5")}</Button>
                    <Button
                      type="button"
                      size="sm"
                      className="!bg-success hover:!bg-success/90"
                      onClick={() => submitVerify(item.listingId, { decision: "APPROVE" })}
                    >
                      {t("legacy.verificationQueuePage.t6")}</Button>
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
          <EmptyState>{t("legacy.verificationQueuePage.t7")}</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
