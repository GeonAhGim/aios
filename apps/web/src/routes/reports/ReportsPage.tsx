import { useReport } from "@aios/shared-hooks";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function ReportsPage() {
  const [periodStart, setPeriodStart] = useState(isoDaysAgo(30));
  const [periodEnd, setPeriodEnd] = useState(isoDaysAgo(0));
  const { data: report, isLoading } = useReport(periodStart, periodEnd);

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">기간별 보고서</h1>

        <div className="flex gap-4">
          <div className="space-y-1">
            <label className="text-sm text-slate-400">시작일</label>
            <input
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">종료일</label>
            <input
              type="date"
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
        </div>

        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : report ? (
          <>
            <div className="grid grid-cols-4 gap-4">
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">총 수익률</p>
                <p className="text-xl font-semibold text-slate-100">{report.totalReturn}%</p>
              </div>
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">승률</p>
                <p className="text-xl font-semibold text-slate-100">
                  {report.winRate ?? "N/A"}
                </p>
              </div>
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">최대 낙폭(MDD)</p>
                <p className="text-xl font-semibold text-slate-100">{report.maxDrawdown}%</p>
              </div>
              <div className="rounded-lg border border-slate-800 p-4">
                <p className="text-sm text-slate-500">거래 횟수</p>
                <p className="text-xl font-semibold text-slate-100">{report.tradeCount}</p>
              </div>
            </div>

            <section className="rounded-lg border border-slate-800 p-6">
              <h2 className="mb-4 text-lg font-medium text-slate-100">전략별 기여도</h2>
              {report.strategyContributions.length > 0 ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-slate-500">
                    <tr>
                      <th className="pb-2">전략</th>
                      <th className="pb-2">실현 손익</th>
                      <th className="pb-2">거래수</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-200">
                    {report.strategyContributions.map((c) => (
                      <tr key={`${c.strategyId}-${c.strategyVersion}`} className="border-t border-slate-800">
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
                <p className="text-slate-500">해당 기간 거래 내역이 없습니다.</p>
              )}
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
