import {
  usePauseExecution,
  useRetireExecution,
  useSetExecutionRiskGuard,
  useStartExecution,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError, type ExecutionCardResponse } from "@aios/shared-types";
import { Button, Input, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../../hooks/useIdempotentSubmit";
import { exchangeLabel } from "../../../lib/exchangeLabels";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { ForbiddenNotice } from "../../../components/ForbiddenNotice";

interface ExecutionCardProps {
  execution: ExecutionCardResponse;
}

// spec §3.3 에러 taxonomy: 재시작 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-901 패턴, task-1048에서 여기 남아있던 직접 렌더를 교체).
function StartExecutionError({ error }: { error: unknown }) {
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

// 16번 문서 §17.5.2 패턴 — pausedBy로 Watchdog 자동정지(SAFETY_LAYER)와
// 사용자 수동정지(USER)를 구분해 표시한다. 이 응답 자체엔 pausedBy 필드가
// 없어(ExecutionCardResponse) status만으로 판단 — PAUSED면 백엔드가
// start() 시점에 SAFETY_LAYER 여부를 검사해 거부하므로, 재시작 시도 후
// 에러 메시지로 실제 원인을 사용자에게 보여준다.
export function ExecutionCard({ execution }: ExecutionCardProps) {
  const start = useStartExecution();
  const pause = usePauseExecution();
  const retire = useRetireExecution();
  const setRiskGuard = useSetExecutionRiskGuard();
  const { submit: submitStart } = useIdempotentSubmit(`executions.start:${execution.executionId}`);
  const [maxDrawdown, setMaxDrawdown] = useState(execution.maxDrawdownPct ?? "");

  const realized = Number(execution.realizedPnl);
  const unrealized = Number(execution.unrealizedPnl);

  async function handleStart() {
    try {
      await submitStart((idempotencyKey) =>
        start.mutateAsync({ executionId: execution.executionId, idempotencyKey }),
      );
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-fg">{execution.strategyId}</p>
          <p className="text-sm text-fg-muted">
            {exchangeLabel(execution.exchange)} · {execution.mode} · 배분{" "}
            {execution.allocatedCapital}
          </p>
        </div>
        <StatusBadge status={execution.status} />
      </div>
      <div className="tabular mt-3 grid grid-cols-2 gap-x-4 text-sm">
        <p className={realized >= 0 ? "text-success" : "text-danger"}>
          실현 손익 {execution.realizedPnl}
        </p>
        <p className={unrealized >= 0 ? "text-success" : "text-danger"}>
          미실현 손익 {execution.unrealizedPnl}
        </p>
      </div>
      {start.isError && <StartExecutionError error={start.error} />}
      {execution.status !== "RETIRED" && (
        <div className="mt-3 flex items-center gap-2 text-xs">
          <span className="text-fg-muted">위험 관리 — 손실 한도(%)</span>
          <Input
            type="number"
            min="0"
            max="100"
            value={maxDrawdown}
            onChange={(e) => setMaxDrawdown(e.target.value)}
            placeholder="비활성"
            className="w-20 py-1"
          />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={setRiskGuard.isPending}
            onClick={() =>
              setRiskGuard.mutate({
                executionId: execution.executionId,
                body: { maxDrawdownPct: maxDrawdown === "" ? null : maxDrawdown },
              })
            }
          >
            적용
          </Button>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        {execution.status !== "RUNNING" && execution.status !== "RETIRED" && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => void handleStart()}
            loading={start.isPending}
          >
            시작
          </Button>
        )}
        {execution.status === "RUNNING" && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => pause.mutate(execution.executionId)}
            loading={pause.isPending}
          >
            일시정지
          </Button>
        )}
        {execution.status !== "RETIRED" && (
          <Button
            type="button"
            variant="danger"
            size="sm"
            onClick={() => retire.mutate({ executionId: execution.executionId })}
            loading={retire.isPending}
          >
            중지
          </Button>
        )}
      </div>
    </div>
  );
}
