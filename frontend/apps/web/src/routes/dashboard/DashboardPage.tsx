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
import { DataFreshness } from "../../components/DataFreshness";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { useTranslation } from "react-i18next";

export function DashboardPage() {
  const { t } = useTranslation();
  const { data: riskProfile } = useRiskProfile();
  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio();
  const { data: executions, isLoading: executionsLoading } = useExecutions();

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader
          title={t("legacy.dashboardPage.title1")}
          action={riskProfile && <Badge tone="accent">{t("legacy.dashboardPage.t2", { riskProfile: riskProfile.riskProfile })}</Badge>}
        />

        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>{t("legacy.dashboardPage.t3")}</CardTitle>
            {/* GET /portfolio는 아직 ApiResponse 봉투 미적용(apiPaths.ts "portfolio.get" ·
                PLT-19 예정)이라 meta.as_of가 없다 — react-query dataUpdatedAt(클라이언트가
                응답을 받은 시각)을 as_of 대신 넣으면 항상 "방금" 취급되어 실제 서버 데이터가
                오래됐어도 stale 배지가 절대 뜨지 않는다(task-936 decision, Date.now() 대입
                금지). 봉투가 붙기 전까지는 null로 두어 "확인 불가"를 정직하게 보여준다. */}
            {portfolio && <DataFreshness asOf={null} />}
          </div>
          {portfolioLoading ? (
            <LoadingState />
          ) : portfolio ? (
            <div className="space-y-6">
              <div className="grid grid-cols-3 gap-4">
                <Stat label={t("legacy.dashboardPage.label4")} value={portfolio.totalPortfolioValue} />
                <Stat label={t("legacy.dashboardPage.label5")} value={portfolio.unallocatedCash} />
                <Stat label={t("legacy.dashboardPage.label6")} value={portfolio.allocations.length} />
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
          <CardTitle>{t("legacy.dashboardPage.t7")}</CardTitle>
          {executionsLoading ? (
            <LoadingState />
          ) : executions && executions.length > 0 ? (
            <ul className="divide-y divide-border">
              {executions.map((exec) => (
                <li key={exec.executionId} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-medium text-fg">{exec.strategyId}</p>
                    <p className="text-sm text-fg-muted">
                      {exchangeLabel(exec.exchange)} · {exec.mode}
                    </p>
                  </div>
                  <StatusBadge status={exec.status} />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>{t("legacy.dashboardPage.t8")}</EmptyState>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
