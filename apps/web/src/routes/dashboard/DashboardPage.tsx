import { useExecutions, usePortfolio, useRiskProfile } from "@aios/shared-hooks";
import {
  AllocationBarChart,
  Badge,
  Card,
  CardTitle,
  EmptyState,
  LoadingState,
  PageHeader,
  Stat,
  StatusBadge,
} from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";

export function DashboardPage() {
  const { data: riskProfile } = useRiskProfile();
  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio();
  const { data: executions, isLoading: executionsLoading } = useExecutions();

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader
          title="대시보드"
          action={riskProfile && <Badge tone="accent">위험등급 {riskProfile.riskProfile}</Badge>}
        />

        <Card>
          <CardTitle>포트폴리오 요약</CardTitle>
          {portfolioLoading ? (
            <LoadingState />
          ) : portfolio ? (
            <div className="space-y-6">
              <div className="grid grid-cols-3 gap-4">
                <Stat label="총 포트폴리오 가치" value={portfolio.totalPortfolioValue} />
                <Stat label="미배분 현금" value={portfolio.unallocatedCash} />
                <Stat label="배분된 실행 수" value={portfolio.allocations.length} />
              </div>
              {portfolio.allocations.length > 0 && (
                <AllocationBarChart
                  allocations={portfolio.allocations.map((a) => ({
                    name: a.strategyId,
                    value: Number(a.weightPct),
                  }))}
                  unallocatedPct={Number(portfolio.unallocatedCashWeightPct)}
                />
              )}
            </div>
          ) : null}
        </Card>

        <Card>
          <CardTitle>실행 중인 전략</CardTitle>
          {executionsLoading ? (
            <LoadingState />
          ) : executions && executions.length > 0 ? (
            <ul className="divide-y divide-border">
              {executions.map((exec) => (
                <li key={exec.executionId} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-medium text-fg">{exec.strategyId}</p>
                    <p className="text-sm text-fg-muted">
                      {exec.exchange} · {exec.mode}
                    </p>
                  </div>
                  <StatusBadge status={exec.status} />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>실행 중인 전략이 없습니다.</EmptyState>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
