import { useEvaluateMandatePolicy } from "@aios/shared-hooks";
import { describeReasonCode } from "@aios/shared-types";
import type { PolicyDecisionView } from "@aios/shared-types";
import { Button, Card, Field, Input, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { MandateActionError } from "./MandateActionError";

// task-2336(FE-OPS-2): MandatesPage에서 분리(P6 300줄 상한). policy:evaluate 트리거
// 패널 — DoD(d): 위반 규칙 코드를 목록으로 표기하고, 빈 결과(reasonCodes=[], 성공)와
// 평가 실패(error)를 서로 다른 분기로 렌더한다(I-10 조용한 성공 금지 — 실패를 "위반
// 없음"으로 뭉개지 않는다).
export function MandatePolicyPanel() {
  const evaluate = useEvaluateMandatePolicy();
  const [commandType, setCommandType] = useState("ORDER_SUBMIT");
  const [result, setResult] = useState<PolicyDecisionView | null>(null);
  const [error, setError] = useState<unknown>(null);

  function handleEvaluate() {
    setResult(null);
    setError(null);
    evaluate.mutate({ commandType }, { onSuccess: setResult, onError: setError });
  }

  return (
    <Card>
      <h2 className="font-medium text-fg">정책 평가(policy:evaluate)</h2>
      <div className="mt-3 flex items-end gap-2">
        <Field label="커맨드 유형(command_type)">
          <Input type="text" value={commandType} onChange={(e) => setCommandType(e.target.value)} />
        </Field>
        <Button type="button" variant="secondary" loading={evaluate.isPending} onClick={handleEvaluate}>
          평가
        </Button>
      </div>
      {error !== null && (
        <div className="mt-3">
          <MandateActionError error={error} />
        </div>
      )}
      {error === null && result && (
        <div className="mt-3 rounded-md border border-border bg-surface-hover p-3 text-sm">
          <p className="font-medium text-fg">
            판정: <StatusBadge status={result.outcome} />
          </p>
          {result.reasonCodes.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-fg-muted">
              {result.reasonCodes.map((code) => (
                <li key={code}>{describeReasonCode(code)}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-fg-muted">위반 규칙 없음.</p>
          )}
        </div>
      )}
    </Card>
  );
}
