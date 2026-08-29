import { usePortfolio, useRebalancePortfolio } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  Alert,
  AllocationBarChart,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Input,
  LoadingState,
  PageHeader,
  Stat,
} from "@aios/ui-web";
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
        <PageHeader title="포트폴리오" />

        {isLoading ? (
          <LoadingState />
        ) : portfolio ? (
          <>
            <div className="grid grid-cols-3 gap-4">
              <Stat label="총 포트폴리오 가치" value={portfolio.totalPortfolioValue} />
              <Stat
                label="미배분 현금"
                value={`${portfolio.unallocatedCash} (${portfolio.unallocatedCashWeightPct}%)`}
              />
              <Stat label="배분된 실행 수" value={portfolio.allocations.length} />
            </div>

            {portfolio.allocations.length > 0 && (
              <Card>
                <CardTitle>자산 배분</CardTitle>
                <AllocationBarChart
                  allocations={portfolio.allocations.map((a) => ({
                    name: a.strategyId,
                    value: Number(a.weightPct),
                  }))}
                  unallocatedPct={Number(portfolio.unallocatedCashWeightPct)}
                />
              </Card>
            )}

            <Card>
              <CardTitle>배분 내역 · 재조정</CardTitle>
              {portfolio.allocations.length > 0 ? (
                <div className="space-y-3">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="text-left text-fg-muted">
                        <tr>
                          <th className="pb-2 font-normal">전략</th>
                          <th className="pb-2 font-normal">비중</th>
                          <th className="pb-2 font-normal">손익</th>
                          <th className="pb-2 font-normal">새 배분 자본</th>
                        </tr>
                      </thead>
                      <tbody className="tabular text-fg">
                        {portfolio.allocations.map((a) => (
                          <tr key={a.executionId} className="border-t border-border">
                            <td className="py-2">{a.strategyId}</td>
                            <td className="py-2">{a.weightPct}%</td>
                            <td className="py-2">{a.totalPnl}</td>
                            <td className="py-2">
                              <Input
                                type="number"
                                placeholder={a.allocatedCapital}
                                value={drafts[a.executionId] ?? ""}
                                onChange={(e) =>
                                  setDrafts((d) => ({ ...d, [a.executionId]: e.target.value }))
                                }
                                className="w-28"
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {error && <Alert>{error}</Alert>}
                  <Button type="button" onClick={handleRebalance} loading={rebalance.isPending}>
                    재조정 적용
                  </Button>
                  {rebalance.data && (
                    <p className="text-sm text-fg-muted">
                      적용됨 {rebalance.data.adjusted}건, 승인대기 {rebalance.data.pendingApproval}건
                    </p>
                  )}
                </div>
              ) : (
                <EmptyState>배분된 실행이 없습니다.</EmptyState>
              )}
            </Card>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
