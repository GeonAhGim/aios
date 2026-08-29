import { useAdminDisputes, useResolveDispute } from "@aios/shared-hooks";
import { Button, EmptyState, Input, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function DisputeManagementPage() {
  const { data: disputes, isLoading } = useAdminDisputes();
  const resolve = useResolveDispute();
  const [reasons, setReasons] = useState<Record<number, string>>({});

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
                          resolve.mutate({
                            disputeId: d.id,
                            body: {
                              decision: "NORMAL_RISK_REALIZATION",
                              reason: reasons[d.id] || "",
                            },
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
                          resolve.mutate({
                            disputeId: d.id,
                            body: {
                              decision: "DELISTED_AND_REFUND",
                              reason: reasons[d.id] || "",
                            },
                          })
                        }
                      >
                        상장폐지+환불
                      </Button>
                    </div>
                  )}
                </div>
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
