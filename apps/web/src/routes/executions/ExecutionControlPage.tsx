import { useCreateExecution, useExecutions } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ExecutionCard } from "./components/ExecutionCard";

export function ExecutionControlPage() {
  const { data: executions, isLoading } = useExecutions();
  const createExecution = useCreateExecution();
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
      await createExecution.mutateAsync({
        strategyId,
        strategyVersion,
        allocatedCapital,
        currency: "USDT",
        exchange,
        mode,
      });
      setStrategyId("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "실행 생성에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <h1 className="text-2xl font-semibold text-slate-100">실행 제어판</h1>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">새 실행 설정</h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <input
              type="text"
              required
              placeholder="전략 ID"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
            <input
              type="text"
              required
              placeholder="버전"
              value={strategyVersion}
              onChange={(e) => setStrategyVersion(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
            <input
              type="number"
              required
              placeholder="배분 자본(USDT)"
              value={allocatedCapital}
              onChange={(e) => setAllocatedCapital(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            >
              <option value="bitget">bitget</option>
            </select>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "PAPER" | "LIVE")}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            >
              <option value="PAPER">PAPER(모의)</option>
              <option value="LIVE">LIVE(실거래)</option>
            </select>
            <button
              type="submit"
              disabled={createExecution.isPending}
              className="col-span-2 rounded bg-slate-100 px-3 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50 md:col-span-1"
            >
              {createExecution.isPending ? "생성 중..." : "실행 생성"}
            </button>
          </form>
          {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
          {createExecution.data?.approvalRequestId && (
            <p className="mt-2 text-sm text-amber-400">
              LIVE 모드 승인 대기 중입니다(요청 #{createExecution.data.approvalRequestId}) — 강제
              대기시간이 지난 뒤 관리자 승인이 필요합니다.
            </p>
          )}
        </section>

        <section className="space-y-4">
          <h2 className="text-lg font-medium text-slate-100">실행 목록</h2>
          {isLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : executions && executions.length > 0 ? (
            <div className="grid grid-cols-2 gap-4">
              {executions.map((exec) => (
                <ExecutionCard key={exec.executionId} execution={exec} />
              ))}
            </div>
          ) : (
            <p className="text-slate-500">실행 중인 전략이 없습니다.</p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
