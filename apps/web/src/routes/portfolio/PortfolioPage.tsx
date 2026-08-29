import { usePortfolio, useRebalancePortfolio } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function PortfolioPage() {
  const { data: portfolio, isLoading } = usePortfolio();
  const rebalance = useRebalancePortfolio();
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function handleRebalance() {
    setError(null);
    const adjustments = Object.entries(drafts)
      .filter(([, v]) => v.trim() !== "")
      .map(([executionId, newAllocatedCapital]) => ({
        executionId: Number(executionId),
        newAllocatedCapital,
      }));
    if (adjustments.length === 0) return;
    try {
      await rebalance.mutateAsync({ adjustments });
      setDrafts({});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "재조정에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">포트폴리오</h1>

        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : portfolio ? (
          <>
            <div className="grid grid-cols-3 gap-4">
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">총 포트폴리오 가치</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.totalPortfolioValue}
                </p>
              </div>
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">미배분 현금</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.unallocatedCash} ({portfolio.unallocatedCashWeightPct}%)
                </p>
              </div>
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">배분된 실행 수</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.allocations.length}
                </p>
              </div>
            </div>

            <section className="rounded-lg border border-slate-800 p-6">
              <h2 className="mb-4 text-lg font-medium text-slate-100">배분 내역 · 재조정</h2>
              {portfolio.allocations.length > 0 ? (
                <div className="space-y-3">
                  <table className="w-full text-sm">
                    <thead className="text-left text-slate-500">
                      <tr>
                        <th className="pb-2">전략</th>
                        <th className="pb-2">비중</th>
                        <th className="pb-2">손익</th>
                        <th className="pb-2">새 배분 자본</th>
                      </tr>
                    </thead>
                    <tbody className="text-slate-200">
                      {portfolio.allocations.map((a) => (
                        <tr key={a.executionId} className="border-t border-slate-800">
                          <td className="py-2">{a.strategyId}</td>
                          <td className="py-2">{a.weightPct}%</td>
                          <td className="py-2">{a.totalPnl}</td>
                          <td className="py-2">
                            <input
                              type="number"
                              placeholder={a.allocatedCapital}
                              value={drafts[a.executionId] ?? ""}
                              onChange={(e) =>
                                setDrafts((d) => ({ ...d, [a.executionId]: e.target.value }))
                              }
                              className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {error && <p className="text-sm text-red-400">{error}</p>}
                  <button
                    type="button"
                    onClick={handleRebalance}
                    disabled={rebalance.isPending}
                    className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50"
                  >
                    {rebalance.isPending ? "재조정 중..." : "재조정 적용"}
                  </button>
                  {rebalance.data && (
                    <p className="text-sm text-slate-400">
                      적용됨 {rebalance.data.adjusted}건, 승인대기 {rebalance.data.pendingApproval}
                      건
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-slate-500">배분된 실행이 없습니다.</p>
              )}
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
