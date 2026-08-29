import { useAdminDisputes, useResolveDispute } from "@aios/shared-hooks";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function DisputeManagementPage() {
  const { data: disputes, isLoading } = useAdminDisputes();
  const resolve = useResolveDispute();
  const [reasons, setReasons] = useState<Record<number, string>>({});

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">분쟁 관리</h1>
        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : disputes && disputes.length > 0 ? (
          <ul className="space-y-3">
            {disputes.map((d) => (
              <li key={d.id} className="rounded-lg border border-slate-800 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-slate-100">
                      분쟁 #{d.id} · 구매 #{d.purchaseId}
                    </p>
                    <p className="text-sm text-slate-500">{d.reason}</p>
                    <p className="text-xs text-slate-600">
                      상태 {d.status} · {new Date(d.createdAt).toLocaleString()}
                    </p>
                  </div>
                  {d.status === "OPEN" && (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        placeholder="처리 사유"
                        value={reasons[d.id] ?? ""}
                        onChange={(e) => setReasons((r) => ({ ...r, [d.id]: e.target.value }))}
                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          resolve.mutate({
                            disputeId: d.id,
                            body: {
                              decision: "NORMAL_RISK_REALIZATION",
                              reason: reasons[d.id] || "",
                            },
                          })
                        }
                        className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800"
                      >
                        정상 리스크 실현(기각)
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          resolve.mutate({
                            disputeId: d.id,
                            body: {
                              decision: "DELISTED_AND_REFUND",
                              reason: reasons[d.id] || "",
                            },
                          })
                        }
                        className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800"
                      >
                        상장폐지+환불
                      </button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">분쟁 건이 없습니다.</p>
        )}
      </div>
    </AppShell>
  );
}
