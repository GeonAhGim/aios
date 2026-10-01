import {
  useExecutions,
  useNotificationHistory,
  usePauseExecution,
  usePortfolio,
  useRiskProfile,
  useStartExecution,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import {
  AllocationBarChart,
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  LoadingState,
  PageHeader,
  Stat,
  StatusBadge,
} from "@aios/ui-web";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { useTranslation } from "react-i18next";

// task-10636 (J9 3-4단계): err.message를 직접 노출하지 않고 routeApiError로 403/그 외를
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(ExecutionCard.tsx의 StartExecutionError와
// 동일 관용, SafetyControlActionError 선례).
function EmergencyStopError({ error }: { error: unknown }) {
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

// task-10636 (J9): 전역 긴급 정지 화면(`/admin/safety-controls`)은 AdminRoute로 막혀
// 비관리자(§0 페르소나)가 접근 불가하다 — useExecutions()가 이미 서버가 호출자 본인
// 소유로 스코프한 목록만 돌려주므로(ExecutionCard의 개별 pause/retire와 동일 신뢰 경계),
// 그 목록의 RUNNING 건 전체에 기존 usePauseExecution을 반복 호출해 "내 운용 전부 정지"를
// 백엔드/권한 모델 변경 없이 제공한다. 정지는 확인 모달 없이 즉시 발동하고(위기 상황에서
// 추가 단계는 사용감 저해, UX_JOURNEYS.md §6.3 J9 기준), 같은 패널에 정지 결과와 재개
// 버튼을 보여준다.
function EmergencyStopPanel({ executions }: { executions: { executionId: number; strategyId: string; status: string }[] }) {
  const { t } = useTranslation();
  const pauseExecution = usePauseExecution();
  const startExecution = useStartExecution();
  const [stoppedIds, setStoppedIds] = useState<number[]>([]);
  const [resumedIds, setResumedIds] = useState<number[]>([]);
  const [stopError, setStopError] = useState<unknown>(null);

  const runningExecutions = executions.filter((e) => e.status === "RUNNING");

  function handleStopAll() {
    setStopError(null);
    setResumedIds([]);
    const ids = runningExecutions.map((e) => e.executionId);
    setStoppedIds(ids);
    ids.forEach((executionId) => {
      pauseExecution.mutate(executionId, { onError: (err) => setStopError(err) });
    });
  }

  function handleResume(executionId: number) {
    void startExecution
      .mutateAsync({ executionId, idempotencyKey: `dashboard-resume-${executionId}-${crypto.randomUUID()}` })
      .then(() => setResumedIds((prev) => [...prev, executionId]))
      .catch((err: unknown) => setStopError(err));
  }

  return (
    <Card>
      <CardTitle>{t("legacy.dashboardPage.emergencyStopTitle")}</CardTitle>
      {stopError !== null && <EmergencyStopError error={stopError} />}
      {stoppedIds.length > 0 ? (
        <ul className="divide-y divide-border">
          {stoppedIds.map((executionId) => {
            const execution = executions.find((e) => e.executionId === executionId);
            const resumed = resumedIds.includes(executionId);
            return (
              <li key={executionId} className="flex items-center justify-between py-3">
                <div>
                  <p className="font-medium text-fg">{execution?.strategyId ?? executionId}</p>
                  <StatusBadge status={resumed ? "RUNNING" : "PAUSED"} />
                </div>
                {!resumed && (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={startExecution.isPending}
                    onClick={() => handleResume(executionId)}
                  >
                    {t("legacy.dashboardPage.emergencyStopResume")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      ) : runningExecutions.length === 0 ? (
        <EmptyState>{t("legacy.dashboardPage.emergencyStopEmpty")}</EmptyState>
      ) : (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-fg-muted">{t("legacy.dashboardPage.emergencyStopHint")}</p>
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={pauseExecution.isPending}
            onClick={handleStopAll}
          >
            {t("legacy.dashboardPage.emergencyStopButton")}
          </Button>
        </div>
      )}
    </Card>
  );
}

export function DashboardPage() {
  const { t } = useTranslation();
  const { data: riskProfile } = useRiskProfile();
  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio();
  const { data: executions, isLoading: executionsLoading } = useExecutions();
  const {
    data: recentNotifications,
    isLoading: notificationsLoading,
    isError: notificationsError,
    error: notificationsErrorObj,
  } = useNotificationHistory();

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader
          title={t("legacy.dashboardPage.title1")}
          action={riskProfile && <Badge tone="accent">{t("legacy.dashboardPage.t2", { riskProfile: riskProfile.riskProfile })}</Badge>}
        />

        {executions && <EmergencyStopPanel executions={executions} />}

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
            <EmptyState
              action={
                <Link to="/onboarding/first-run">
                  <Button type="button" variant="secondary" size="sm">
                    {t("onboarding.emptyStateCta")}
                  </Button>
                </Link>
              }
            >
              {t("legacy.dashboardPage.t8")}
            </EmptyState>
          )}
        </Card>

        <Card>
          <CardTitle>{t("legacy.dashboardPage.t9")}</CardTitle>
          {notificationsError ? (
            <ErrorMessage
              errorCode={notificationsErrorObj instanceof ApiError ? notificationsErrorObj.errorCode : undefined}
              message={notificationsErrorObj instanceof Error ? notificationsErrorObj.message : undefined}
            />
          ) : notificationsLoading || !recentNotifications ? (
            <LoadingState />
          ) : recentNotifications.length === 0 ? (
            <EmptyState>{t("legacy.dashboardPage.t10")}</EmptyState>
          ) : (
            <ul className="divide-y divide-border">
              {recentNotifications.slice(0, 5).map((notification, i) => (
                <li key={i} className="flex items-center justify-between py-3">
                  <p className="font-medium text-fg">{notification.eventType}</p>
                  <StatusBadge status={notification.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
