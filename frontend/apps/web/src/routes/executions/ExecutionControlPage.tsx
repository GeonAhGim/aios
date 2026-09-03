import { useCreateExecution, useExecutions } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  classifyServerError,
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
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";
import { useConflictRetry } from "../../hooks/useConflictRetry";
import { ExecutionCard } from "./components/ExecutionCard";

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

export function ExecutionControlPage() {
  const { data: executions, isLoading, refetch } = useExecutions();
  const createExecution = useCreateExecution();
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
    try {
      await createExecutionWithRetry();
      setStrategyId("");
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof ApiError ? err : new Error("실행 생성에 실패했습니다."));
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submitExecution();
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="실행 제어판" />

        <Card>
          <CardTitle>새 실행 설정</CardTitle>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Field label="전략 ID">
              <Input required value={strategyId} onChange={(e) => setStrategyId(e.target.value)} />
            </Field>
            <Field label="버전">
              <Input
                required
                value={strategyVersion}
                onChange={(e) => setStrategyVersion(e.target.value)}
              />
            </Field>
            <Field label="배분 자본(USDT)">
              <Input
                type="number"
                required
                value={allocatedCapital}
                onChange={(e) => setAllocatedCapital(e.target.value)}
              />
            </Field>
            <Field label="거래소">
              <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
                <option value="bitget">bitget</option>
              </Select>
            </Field>
            <Field label="모드">
              <Select value={mode} onChange={(e) => setMode(e.target.value as "PAPER" | "LIVE")}>
                <option value="PAPER">PAPER(모의)</option>
                <option value="LIVE">LIVE(실거래)</option>
              </Select>
            </Field>
            <div className="col-span-2 flex items-end md:col-span-1">
              <Button type="submit" loading={createExecution.isPending} className="w-full">
                실행 생성
              </Button>
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
                LIVE 모드 승인 대기 중입니다(요청 #{createExecution.data.approvalRequestId}) —
                강제 대기시간이 지난 뒤 관리자 승인이 필요합니다.
              </Alert>
            </div>
          )}
        </Card>

        <section className="space-y-4">
          <h2 className="text-lg font-medium text-fg">실행 목록</h2>
          {isLoading ? (
            <LoadingState />
          ) : executions && executions.length > 0 ? (
            <div className="grid grid-cols-2 gap-4">
              {executions.map((exec) => (
                <ExecutionCard key={exec.executionId} execution={exec} />
              ))}
            </div>
          ) : (
            <EmptyState>실행 중인 전략이 없습니다.</EmptyState>
          )}
        </section>
      </div>
    </AppShell>
  );
}
