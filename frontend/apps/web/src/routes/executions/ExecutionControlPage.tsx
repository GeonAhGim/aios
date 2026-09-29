import { useCreateExecution, useEvaluateRiskGate, useExecutions } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  classifyServerError,
  isResourceNotFound,
  routeApiError,
} from "@aios/shared-types";
import {
  Alert,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { NotFoundState } from "../../components/NotFoundState";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";
import { useConflictRetry } from "../../hooks/useConflictRetry";
import { isFeatureEnabled } from "../../lib/featureFlags";
import { ExecutionCard } from "./components/ExecutionCard";
import { RiskVerdictPanel } from "./components/RiskVerdictPanel";
import { useTranslation } from "react-i18next";

// spec §3.3 에러 taxonomy: 실행 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901). classifyServerError
// (task-937)가 EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY(재시도 가능)로 판정할 때만
// onRetry를 넘겨 "다시 시도" 버튼을 보여준다 — EXCHANGE_FATAL/INTERNAL_ERROR(재시도
// 불가)는 onRetry가 없어도 ErrorMessage 자체가 classifyRetry로 버튼을 숨긴다.
function CreateExecutionError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}

// spec §3.3 RESOURCE_NOT_FOUND(404)는 재시도 배너가 아니라 NotFoundState로 렌더한다
// (task-1056/ListingDetailPage와 동일 패턴). 그 외 에러는 classifyServerError로
// 재시도 가능 여부를 판정해 ErrorMessage에 onRetry를 넘긴다.
function ExecutionsListError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation();
  if (isResourceNotFound(error)) {
    return (
      <NotFoundState
        title={t("legacy.executionControlPage.title1")}
        description="삭제되었거나 존재하지 않는 데이터입니다."
      />
    );
  }
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}

export function ExecutionControlPage() {
  const { t } = useTranslation();
  const {
    data: executions,
    isLoading,
    refetch,
    error: executionsError,
    isError: executionsIsError,
  } = useExecutions();
  const createExecution = useCreateExecution();
  const evaluateRiskGate = useEvaluateRiskGate();
  const { submit } = useIdempotentSubmit("executions.create");
  const [strategyId, setStrategyId] = useState("");
  const [strategyVersion, setStrategyVersion] = useState("1.0.0");
  const [allocatedCapital, setAllocatedCapital] = useState("100");
  const [exchange, setExchange] = useState("bitget");
  const [mode, setMode] = useState<"PAPER" | "LIVE">("PAPER");
  const [error, setError] = useState<unknown>(null);

  // §3.3 STATE_CONCURRENCY_CONFLICT(409)는 useConflictRetry(task-937)로 실행 목록을
  // 재조회한 뒤 1회 재제출한다. submit()을 다시 호출하므로(useIdempotentSubmit이 409를
  // 4xx로 보고 이전 키를 이미 폐기해둔 뒤) 재제출은 새 Idempotency-Key로 나간다
  // (task-383 classifyIdempotencyFailure 규칙).
  const { run: createExecutionWithRetry } = useConflictRetry(
    () =>
      submit((idempotencyKey) =>
        createExecution.mutateAsync({
          body: {
            strategyId,
            strategyVersion,
            allocatedCapital,
            currency: "USDT",
            exchange,
            mode,
          },
          idempotencyKey,
        }),
      ),
    refetch,
  );

  async function submitExecution() {
    setError(null);
    // task-7500(J3 G-4): RiskVerdictPanel이 evaluateRiskGate(PRE_SUBMIT)의 실제
    // RiskEvaluationView를 그대로 보여준다 — 플래그가 꺼져 있으면(기본값) 부가
    // 네트워크 호출 자체를 내지 않는다(FeatureFlagGate와 동일 "부작용 없음" 원칙).
    if (isFeatureEnabled("FF_J3_RISK_PANEL")) {
      evaluateRiskGate.mutate({ gateKind: "PRE_SUBMIT" });
    }
    try {
      await createExecutionWithRetry();
      setStrategyId("");
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof ApiError ? err : new Error(t("legacy.executionControlPage.t15")));
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submitExecution();
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title={t("legacy.executionControlPage.title2")} />

        <Card>
          <CardTitle>{t("legacy.executionControlPage.t3")}</CardTitle>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Field label={t("legacy.executionControlPage.label4")}>
              <Input required value={strategyId} onChange={(e) => setStrategyId(e.target.value)} />
            </Field>
            <Field label={t("legacy.executionControlPage.label5")}>
              <Input
                required
                value={strategyVersion}
                onChange={(e) => setStrategyVersion(e.target.value)}
              />
            </Field>
            <Field label={t("legacy.executionControlPage.label6")}>
              <Input
                type="number"
                required
                value={allocatedCapital}
                onChange={(e) => setAllocatedCapital(e.target.value)}
              />
            </Field>
            <Field label={t("legacy.executionControlPage.label7")}>
              <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
                <option value="bitget">bitget</option>
              </Select>
            </Field>
            <Field label={t("legacy.executionControlPage.label8")}>
              <Select value={mode} onChange={(e) => setMode(e.target.value as "PAPER" | "LIVE")}>
                <option value="PAPER">{t("legacy.executionControlPage.t9")}</option>
                <option value="LIVE">{t("legacy.executionControlPage.t10")}</option>
              </Select>
            </Field>
            <div className="col-span-2 flex items-end md:col-span-1">
              <Button type="submit" loading={createExecution.isPending} className="w-full">
                {t("legacy.executionControlPage.t11")}</Button>
            </div>
          </form>
          {error !== null && (
            <div className="mt-3">
              <CreateExecutionError error={error} onRetry={() => void submitExecution()} />
            </div>
          )}
          {createExecution.data?.approvalRequestId && (
            <div className="mt-3">
              <Alert tone="warning">
                {t("legacy.executionControlPage.t12", { approvalRequestId: createExecution.data.approvalRequestId })}</Alert>
            </div>
          )}
        </Card>

        <RiskVerdictPanel
          status={evaluateRiskGate.status}
          data={evaluateRiskGate.data}
          error={evaluateRiskGate.error}
        />

        <section className="space-y-4">
          <h2 className="text-lg font-medium text-fg">{t("legacy.executionControlPage.t13")}</h2>
          {executionsIsError ? (
            <ExecutionsListError error={executionsError} onRetry={() => void refetch()} />
          ) : isLoading ? (
            <LoadingState />
          ) : executions && executions.length > 0 ? (
            <div className="grid grid-cols-2 gap-4">
              {executions.map((exec) => (
                <ExecutionCard key={exec.executionId} execution={exec} />
              ))}
            </div>
          ) : (
            <EmptyState>{t("legacy.executionControlPage.t14")}</EmptyState>
          )}
        </section>
      </div>
    </AppShell>
  );
}
