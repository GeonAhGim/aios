import { usePauseExecution, useRetireExecution, useStartExecution } from "@aios/shared-hooks";
import type { ExecutionCardResponse } from "@aios/shared-types";

interface ExecutionCardProps {
  execution: ExecutionCardResponse;
}

// 16번 문서 §17.5.2 패턴 — pausedBy로 Watchdog 자동정지(SAFETY_LAYER)와
// 사용자 수동정지(USER)를 구분해 표시한다. 이 응답 자체엔 pausedBy 필드가
// 없어(ExecutionCardResponse) status만으로 판단 — PAUSED면 백엔드가
// start() 시점에 SAFETY_LAYER 여부를 검사해 거부하므로, 재시작 시도 후
// 에러 메시지로 실제 원인을 사용자에게 보여준다.
export function ExecutionCard({ execution }: ExecutionCardProps) {
  const start = useStartExecution();
  const pause = usePauseExecution();
  const retire = useRetireExecution();

  return (
    <div className="rounded-lg border border-slate-800 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-slate-100">{execution.strategyId}</p>
          <p className="text-sm text-slate-500">
            {execution.exchange} · {execution.mode} · 배분 {execution.allocatedCapital}
          </p>
        </div>
        <span className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300">
          {execution.status}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 text-sm text-slate-400">
        <p>실현 손익: {execution.realizedPnl}</p>
        <p>미실현 손익: {execution.unrealizedPnl}</p>
      </div>
      {start.isError && (
        <p className="mt-2 text-xs text-red-400">{(start.error as Error).message}</p>
      )}
      <div className="mt-3 flex gap-2">
        {execution.status !== "RUNNING" && execution.status !== "RETIRED" && (
          <button
            type="button"
            onClick={() => start.mutate(execution.executionId)}
            disabled={start.isPending}
            className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
          >
            시작
          </button>
        )}
        {execution.status === "RUNNING" && (
          <button
            type="button"
            onClick={() => pause.mutate(execution.executionId)}
            disabled={pause.isPending}
            className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
          >
            일시정지
          </button>
        )}
        {execution.status !== "RETIRED" && (
          <button
            type="button"
            onClick={() => retire.mutate({ executionId: execution.executionId })}
            disabled={retire.isPending}
            className="rounded border border-red-900 px-3 py-1 text-sm text-red-400 hover:bg-red-950 disabled:opacity-50"
          >
            중지
          </button>
        )}
      </div>
    </div>
  );
}
