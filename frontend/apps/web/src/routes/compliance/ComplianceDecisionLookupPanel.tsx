import { useComplianceDecisionLookup } from "@aios/shared-hooks";
import type { ComplianceDecisionView } from "@aios/shared-types";
import { Button, Card, Field, Input, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ComplianceActionError } from "./ComplianceActionError";

// task-2668(CM-18): spec L4_compliance_and_regulatory_v1.0.md §9 CM-18 DoD
// ("판정 조회·규칙 히트") — task-2618(CM-17 API)이 배선한 GET
// /v1/foundation/compliance/decisions/{decision_id}(explain, CM-13)를 그대로
// 노출한다. ComplianceDecisionPanel(CM-17 프론트, task-2620)은 그 시점엔 이 API가
// 없어 policy:evaluate 위에서 reasonCodes 평면 목록만 보여줬지만(그 파일 상단
// 주석 참조), 이 패널은 구조화된 ComplianceDecision/RuleHit(rule_id·severity·
// evidence)을 렌더한다. 판정 로직 재구현 없음 — 서버가 준 값을 그대로 보여준다
// (CM-A5, mandates.ts와 동일 decision).
//
// "예외 승인"(spec §3 — 운영자 사전 예외, 1회성 서버측 토큰 I-11 + 사유 필수 +
// 별도 감사)은 이 리프에 없다: evaluate_pre_trade.py를 포함해 어떤 라우터에도
// CM_EXCEPTION_REQUIRED/예외 토큰 발급·소비 경로가 아직 없다(grep 확인 완료).
// 이를 실제로 구현하려면 policy_decision.outcome CHECK 제약 확장이나 새 테이블이
// 필요해(마이그레이션 필요) 순수 프론트 리프의 범위를 벗어난다 — CM-17(task-2620)도
// 같은 이유로 스킵했다(§9 CM-17 DoD "예외 토큰 흐름" 미달성인 채로 done 처리된
// 선례). 백엔드 리프가 먼저 그 경로를 만들어야 이 화면에 승인 액션을 추가할 수
// 있다.
export function ComplianceDecisionLookupPanel() {
  const { t } = useTranslation();
  const lookup = useComplianceDecisionLookup();
  const [decisionId, setDecisionId] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [result, setResult] = useState<ComplianceDecisionView | null>(null);
  const [error, setError] = useState<unknown>(null);

  function handleLookup() {
    const trimmed = decisionId.trim();
    if (trimmed === "") {
      setValidationError(t("legacy.complianceDecisionLookupPanel.t4"));
      setResult(null);
      setError(null);
      return;
    }
    setValidationError(null);
    setResult(null);
    setError(null);
    lookup.mutate(trimmed, { onSuccess: setResult, onError: setError });
  }

  return (
    <Card>
      <h2 className="font-medium text-fg">{t("legacy.complianceDecisionLookupPanel.t1")}</h2>
      <div className="mt-3 flex items-end gap-2">
        <Field label={t("legacy.complianceDecisionLookupPanel.label2")}>
          <Input type="text" value={decisionId} onChange={(e) => setDecisionId(e.target.value)} />
        </Field>
        <Button type="button" variant="secondary" loading={lookup.isPending} onClick={handleLookup}>
          {t("legacy.complianceDecisionLookupPanel.t3")}
        </Button>
      </div>

      {validationError !== null && <p className="mt-2 text-sm text-danger">{validationError}</p>}

      {error !== null && (
        <div className="mt-3">
          <ComplianceActionError error={error} onRetry={handleLookup} />
        </div>
      )}

      {error === null && result && (
        <div className="mt-3 rounded-md border border-border bg-surface-hover p-3 text-sm">
          <p className="font-medium text-fg">
            {t("legacy.complianceDecisionLookupPanel.t5")}
            <StatusBadge status={result.verdict} />
          </p>

          <div className="mt-2">
            <h3 className="text-xs font-medium uppercase text-fg-muted">
              {t("legacy.complianceDecisionLookupPanel.t6")}
            </h3>
            {result.ruleHits.length > 0 ? (
              <ul className="mt-1 space-y-2">
                {result.ruleHits.map((hit, index) => (
                  <li key={`${hit.ruleId}-${index}`} className="rounded border border-border p-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-fg-muted">{hit.ruleId}</span>
                      <StatusBadge status={hit.severity} />
                    </div>
                    <p className="mt-1 text-fg-muted">{hit.message}</p>
                    {Object.keys(hit.evidence).length > 0 && (
                      <pre className="mt-1 whitespace-pre-wrap break-all text-xs text-fg-muted">
                        {JSON.stringify(hit.evidence)}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-fg-muted">{t("legacy.complianceDecisionLookupPanel.t7")}</p>
            )}
          </div>

          <div className="mt-3 border-t border-border pt-2">
            <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-fg-muted">
              <dt>{t("legacy.complianceDecisionLookupPanel.t8")}</dt>
              <dd className="break-all">{result.bundleVersion}</dd>
              <dt>{t("legacy.complianceDecisionLookupPanel.t9")}</dt>
              <dd>{result.evaluatedAt}</dd>
            </dl>
          </div>
        </div>
      )}
    </Card>
  );
}
