import { useStartValidation } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  type ValidationOutcome,
  type ValidationResultView,
} from "@aios/shared-types";
import { Button, Card } from "@aios/ui-web";
import { useState } from "react";
import { BadRequestNotice } from "../../../components/BadRequestNotice";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { ForbiddenNotice } from "../../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 검증 실행 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(StrategyBuilderPage의
// StrategyActionError와 동일 패턴, task-901).
function ValidationRunError({ error }: { error: unknown }) {
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

const OUTCOME_LABELS: Record<ValidationOutcome, string> = {
  PASS: "통과",
  FAIL: "불합격",
  PASS_WITH_OBLIGATIONS: "조건부 통과",
};

function outcomeLabel(result: ValidationResultView): string {
  if (!result.outcome) return `대기 중 (${result.state})`;
  return `${OUTCOME_LABELS[result.outcome]} (${result.state})`;
}

// 76번 §4 StartValidation — 이미 만들어진 전략(strategyId/strategyVersion)에
// 대해 신규 데이터 경로 없이(기존 candles와 동일한 CredentialResolver+adapter
// 경로) 검증을 1회 실행하고 판정을 보여준다. 조건 편집·저장과 결과를 섞지 않기
// 위해 별도 패널로 둔다(StrategyWizardPanel과 동일한 구성 결정).
export function ValidationRunPanel({
  strategyId,
  strategyVersion,
  exchange,
  symbol,
}: {
  strategyId: string;
  strategyVersion: string;
  exchange: string;
  symbol: string;
}) {
  const startValidation = useStartValidation();
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<ValidationResultView | null>(null);

  async function handleRun() {
    setError(null);
    setResult(null);
    try {
      const view = await startValidation.mutateAsync({
        strategyId,
        strategyVersion,
        body: {
          exchange,
          symbol,
          costModelFeeBps: 10,
          costModelSlippageBps: 5,
        },
      });
      if (view === null) {
        setError(new Error("검증 결과 형식을 확인할 수 없습니다."));
        return;
      }
      setResult(view);
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("전략 검증 실행에 실패했습니다."));
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-fg">전략 검증 실행</h2>
        <Button
          type="button"
          variant="secondary"
          onClick={handleRun}
          loading={startValidation.isPending}
          disabled={!strategyId.trim()}
        >
          검증 실행
        </Button>
      </div>

      {error !== null && <ValidationRunError error={error} />}

      {result && (
        <div className="space-y-2 rounded-lg border border-border-strong bg-bg p-4 text-sm">
          <p className="font-medium text-fg">판정: {outcomeLabel(result)}</p>
          {result.hard_fail_reasons.length > 0 && (
            <div>
              <p className="text-fg-secondary">불합격 사유</p>
              <ul className="list-disc pl-5 text-fg-muted">
                {result.hard_fail_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {result.warnings.length > 0 && (
            <div>
              <p className="text-fg-secondary">경고</p>
              <ul className="list-disc pl-5 text-fg-muted">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
