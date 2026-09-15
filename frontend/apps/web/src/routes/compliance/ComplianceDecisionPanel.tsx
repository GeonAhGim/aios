import { useEvaluateMandatePolicy } from "@aios/shared-hooks";
import { describeReasonCode } from "@aios/shared-types";
import type { PolicyDecisionView } from "@aios/shared-types";
import { Badge, Button, Card, Field, Input, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { ComplianceActionError } from "./ComplianceActionError";

// task-2620(H-2 CM-17 프론트): CM-18 DoD("판정 조회·규칙 히트")를 policy:evaluate
// 위에서 구현한다 — CM-17 전용 API는 아직 없다(§9 진행 현황 inflight), spec 3장의
// ComplianceDecision/RuleHit도 아직 어떤 라우터도 반환하지 않는다(policy_decision.
// reason_codes 1:1 매핑만 존재). 그래서 여기서도 MandatePolicyPanel과 같은
// PolicyDecisionView를 쓰되, outcome을 ALLOW/WARN/DENY로 재판정하지 않고 서버가 준
// 값을 그대로 보여준다 — 판정 로직 재구현 금지(mandates.ts 주석, CM-A5 권위 분리).
//
// DoD: 위반 규칙 목록(=판정 목록)과 obligations/evaluated_at/expires_at(=설명)을
// 함께 보여주고, 빈 결과(reasonCodes=[], 성공)와 평가 실패(error)를 서로 다른
// 분기로 렌더한다(MandatePolicyPanel과 동일 결정 — 실패를 "위반 없음"으로 뭉개지 않는다).
export function ComplianceDecisionPanel() {
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
      <h2 className="font-medium text-fg">판정 조회(policy:evaluate)</h2>
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
          <ComplianceActionError error={error} onRetry={handleEvaluate} />
        </div>
      )}

      {error === null && result && (
        <div className="mt-3 rounded-md border border-border bg-surface-hover p-3 text-sm">
          <p className="font-medium text-fg">
            판정: <StatusBadge status={result.outcome} />
          </p>

          <div className="mt-2">
            <h3 className="text-xs font-medium uppercase text-fg-muted">규칙 히트</h3>
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

          <div className="mt-3 border-t border-border pt-2">
            <h3 className="text-xs font-medium uppercase text-fg-muted">설명</h3>
            <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-fg-muted">
              <dt>번들</dt>
              <dd>{result.bundleId}</dd>
              <dt>판정 시각</dt>
              <dd>{result.evaluatedAt}</dd>
              <dt>만료 시각</dt>
              <dd>{result.expiresAt ?? "만료 없음"}</dd>
              <dt>의무(obligations)</dt>
              <dd>
                {result.obligations.length > 0 ? (
                  result.obligations.map((obligation) => (
                    <Badge key={obligation} tone="neutral" className="mr-1">
                      {obligation}
                    </Badge>
                  ))
                ) : (
                  "없음"
                )}
              </dd>
            </dl>
          </div>
        </div>
      )}
    </Card>
  );
}
