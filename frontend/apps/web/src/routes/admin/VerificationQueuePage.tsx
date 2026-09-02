import { useVerificationQueue, useVerifyListing } from "@aios/shared-hooks";
import { Button, EmptyState, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function VerificationQueuePage() {
  const { data: queue, isLoading } = useVerificationQueue();
  const verify = useVerifyListing();
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});

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
                        verify.mutate({
                          listingId: item.listingId,
                          body: {
                            decision: "REJECT",
                            rejectionReason: rejectReasons[item.listingId] || "사유 미기재",
                          },
                        })
                      }
                    >
                      반려
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="!bg-success hover:!bg-success/90"
                      onClick={() =>
                        verify.mutate({ listingId: item.listingId, body: { decision: "APPROVE" } })
                      }
                    >
                      승인
                    </Button>
                  </div>
                </div>
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
