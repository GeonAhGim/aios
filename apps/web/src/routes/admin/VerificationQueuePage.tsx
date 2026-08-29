import { useVerificationQueue, useVerifyListing } from "@aios/shared-hooks";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function VerificationQueuePage() {
  const { data: queue, isLoading } = useVerificationQueue();
  const verify = useVerifyListing();
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">전략 검수 대기열</h1>
        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : queue && queue.length > 0 ? (
          <ul className="space-y-3">
            {queue.map((item) => (
              <li key={item.listingId} className="rounded-lg border border-slate-800 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-slate-100">
                      {item.strategyId}@{item.strategyVersion}
                    </p>
                    <p className="text-sm text-slate-500">
                      가격 {item.price ?? "미정"} · 제출일{" "}
                      {new Date(item.submittedAt).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="반려 사유"
                      value={rejectReasons[item.listingId] ?? ""}
                      onChange={(e) =>
                        setRejectReasons((r) => ({ ...r, [item.listingId]: e.target.value }))
                      }
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        verify.mutate({
                          listingId: item.listingId,
                          body: {
                            decision: "REJECT",
                            rejectionReason: rejectReasons[item.listingId] || "사유 미기재",
                          },
                        })
                      }
                      className="rounded border border-red-900 px-3 py-1 text-sm text-red-400 hover:bg-red-950"
                    >
                      반려
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        verify.mutate({ listingId: item.listingId, body: { decision: "APPROVE" } })
                      }
                      className="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600"
                    >
                      승인
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">대기 중인 검수 건이 없습니다.</p>
        )}
      </div>
    </AppShell>
  );
}
