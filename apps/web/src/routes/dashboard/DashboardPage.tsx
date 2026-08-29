import { useExecutions, usePortfolio, useRiskProfile } from "@aios/shared-hooks";
import { AppShell } from "../../components/layout/AppShell";

export function DashboardPage() {
  const { data: riskProfile } = useRiskProfile();
  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio();
  const { data: executions, isLoading: executionsLoading } = useExecutions();

  return (
    <AppShell>
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">대시보드</h1>
          {riskProfile && (
            <p className="mt-1 text-sm text-slate-400">
              현재 위험등급:{" "}
              <span className="font-medium text-slate-200">{riskProfile.riskProfile}</span>
            </p>
          )}
        </div>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">포트폴리오 요약</h2>
          {portfolioLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : portfolio ? (
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-slate-500">총 포트폴리오 가치</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.totalPortfolioValue}
                </p>
              </div>
              <div>
                <p className="text-slate-500">미배분 현금</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.unallocatedCash}
                </p>
              </div>
              <div>
                <p className="text-slate-500">배분된 실행 수</p>
                <p className="text-xl font-semibold text-slate-100">
                  {portfolio.allocations.length}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-slate-500">데이터가 없습니다.</p>
          )}
        </section>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">실행 중인 전략</h2>
          {executionsLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : executions && executions.length > 0 ? (
            <ul className="divide-y divide-slate-800">
              {executions.map((exec) => (
                <li key={exec.executionId} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-medium text-slate-100">{exec.strategyId}</p>
                    <p className="text-sm text-slate-500">
                      {exec.exchange} · {exec.mode}
                    </p>
                  </div>
                  <span className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300">
                    {exec.status}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">실행 중인 전략이 없습니다.</p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
