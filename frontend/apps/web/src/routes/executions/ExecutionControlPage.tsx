import { useCreateExecution, useExecutions } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
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
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";
import { ExecutionCard } from "./components/ExecutionCard";

export function ExecutionControlPage() {
  const { data: executions, isLoading } = useExecutions();
  const createExecution = useCreateExecution();
  const { submit } = useIdempotentSubmit("executions.create");
  const [strategyId, setStrategyId] = useState("");
  const [strategyVersion, setStrategyVersion] = useState("1.0.0");
  const [allocatedCapital, setAllocatedCapital] = useState("100");
  const [exchange, setExchange] = useState("bitget");
  const [mode, setMode] = useState<"PAPER" | "LIVE">("PAPER");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await submit((idempotencyKey) =>
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
      );
      setStrategyId("");
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof ApiError ? err.message : "실행 생성에 실패했습니다.");
    }
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
          {error && <div className="mt-3"><Alert>{error}</Alert></div>}
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
