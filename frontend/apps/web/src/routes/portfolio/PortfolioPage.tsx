import { usePortfolio, useRebalancePortfolio } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import {
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
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";
import { PortfolioPositionsSection } from "./PortfolioPositionsSection";

// spec §3.3 에러 taxonomy: 재조정 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901).
function RebalanceError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function PortfolioPage() {
  const { data: portfolio, isLoading, isError, error: portfolioError, refetch } = usePortfolio();
  const rebalance = useRebalancePortfolio();
  const { submit } = useIdempotentSubmit("portfolio.rebalance");
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [error, setError] = useState<unknown>(null);
  // spec §3.3 5xx: 조회(GET /portfolio) 실패는 재조정 에러와 별개로 routeApiError로
  // 판정한다(task-937 규약 재사용) — EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY만
  // refetch() 재시도 버튼을 보여주고 EXCHANGE_FATAL은 안내만 한다.
  const routedPortfolioError = isError ? routeApiError(portfolioError) : null;
  const canRetryPortfolio =
    routedPortfolioError?.kind === "refetch_retry" || routedPortfolioError?.kind === "backoff_retry";

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
      await submit((idempotencyKey) =>
        rebalance.mutateAsync({ body: { adjustments }, idempotencyKey }),
      );
      setDrafts({});
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof ApiError ? err : new Error("재조정에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title="포트폴리오"
          // GET /portfolio는 아직 ApiResponse 봉투 미적용(apiPaths.ts "portfolio.get" ·
          // PLT-19 예정)이라 meta.as_of가 없다 — dataUpdatedAt(react-query가 응답을 받은
          // 시각)을 as_of 대신 쓰면 항상 fresh로 보여 stale 배지가 절대 뜨지 않는다
          // (task-936 decision, Date.now() 대입 금지). 봉투가 붙기 전까지 null로 두어
          // "확인 불가"를 정직하게 보여준다.
          action={portfolio && <DataFreshness asOf={null} />}
        />

        {isError ? (
          <ErrorMessage
            errorCode={portfolioError instanceof ApiError ? portfolioError.errorCode : undefined}
            message={portfolioError instanceof Error ? portfolioError.message : undefined}
            traceId={portfolioError instanceof ApiError ? portfolioError.traceId : undefined}
            retryAfterSec={
              routedPortfolioError?.kind === "backoff_retry" ? routedPortfolioError.afterSec : undefined
            }
            onRetry={canRetryPortfolio ? () => refetch() : undefined}
          />
        ) : isLoading ? (
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
                  {error !== null && <RebalanceError error={error} />}
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

            {/* spec §3.2 (B) 포지션/PnL 분해/NAV — 서버 라우트가 아직 없어(task-628
                decision) 실 데이터 연결 전까지 빈 상태로 배선만 해둔다. */}
            <PortfolioPositionsSection positions={[]} />
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
