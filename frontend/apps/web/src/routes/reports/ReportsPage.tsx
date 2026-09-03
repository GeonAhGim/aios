import { useReport } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Card, CardTitle, EmptyState, Input, LoadingState, PageHeader, PnlChart, Stat } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

// spec §3.3 에러 taxonomy: 보고서 조회 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-1155).
function ReportError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

export function ReportsPage() {
  const [periodStart, setPeriodStart] = useState(isoDaysAgo(30));
  const [periodEnd, setPeriodEnd] = useState(isoDaysAgo(0));
  const { data: report, isLoading, isError, error, refetch } = useReport(periodStart, periodEnd);

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="기간별 보고서" />

        <div className="flex gap-4">
          <label className="space-y-1.5">
            <span className="block text-sm font-medium text-fg-secondary">시작일</span>
            <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </label>
          <label className="space-y-1.5">
            <span className="block text-sm font-medium text-fg-secondary">종료일</span>
            <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </label>
        </div>

        {isError ? (
          <ReportError error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <LoadingState />
        ) : report ? (
          <>
            <div className="grid grid-cols-4 gap-4">
              <Stat
                label="총 수익률"
                value={`${report.totalReturn}%`}
                tone={Number(report.totalReturn) >= 0 ? "success" : "danger"}
              />
              <Stat label="승률" value={report.winRate ?? "N/A"} />
              <Stat label="최대 낙폭(MDD)" value={`${report.maxDrawdown}%`} tone="danger" />
              <Stat label="거래 횟수" value={report.tradeCount} />
            </div>

            {report.dailyPnl.length > 0 && (
              <Card>
                <CardTitle>손익 추이</CardTitle>
                <PnlChart
                  data={report.dailyPnl.map((d) => ({
                    tradeDate: d.tradeDate,
                    dailyPnl: Number(d.dailyPnl),
                    cumulativePnl: Number(d.cumulativePnl),
                  }))}
                />
              </Card>
            )}

            <Card>
              <CardTitle>전략별 기여도</CardTitle>
              {report.strategyContributions.length > 0 ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-fg-muted">
                    <tr>
                      <th className="pb-2 font-normal">전략</th>
                      <th className="pb-2 font-normal">실현 손익</th>
                      <th className="pb-2 font-normal">거래수</th>
                    </tr>
                  </thead>
                  <tbody className="tabular text-fg">
                    {report.strategyContributions.map((c) => (
                      <tr key={`${c.strategyId}-${c.strategyVersion}`} className="border-t border-border">
                        <td className="py-2">
                          {c.strategyId}@{c.strategyVersion}
                        </td>
                        <td className="py-2">{c.realizedPnl}</td>
                        <td className="py-2">{c.tradeCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState>해당 기간 거래 내역이 없습니다.</EmptyState>
              )}
            </Card>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
