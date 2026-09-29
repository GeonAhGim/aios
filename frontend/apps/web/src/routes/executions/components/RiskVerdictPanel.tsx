import { describeReasonCode, extractReasonCodes, type RiskEvaluationView } from "@aios/shared-types";
import { Alert, Card, CardTitle, StatusBadge } from "@aios/ui-web";
import { useTranslation } from "react-i18next";
import { isFeatureEnabled } from "../../../lib/featureFlags";

// task-7500 (J3 G-4): UX_JOURNEYS.md 갭 G-4 — ExecutionControlPage에는 지금까지
// riskGate/verdict/reasonCode 전용 패널이 없었고, 거부는 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 일반 오류 배너로만 표면화됐다. 이 패널은 실행 생성
// 제출과 함께 트리거된 useEvaluateRiskGate(PRE_SUBMIT)의 실제 RiskEvaluationView
// 봉투(outcome/reasonCodes/ruleVersion/evaluatedAt, riskGate.ts 1:1)를 그대로
// 보여준다 — 판정 로직을 프론트에서 재구현하지 않는다(CM-A5 권위 분리와 동일 결정).
// evaluate 호출 자체가 실패하면(네트워크/서버 오류) ALLOW를 절대 보여주지 않고
// fail-closed 문구로 수렴한다.
export type RiskVerdictStatus = "idle" | "pending" | "error" | "success";

export interface RiskVerdictPanelProps {
  status: RiskVerdictStatus;
  data?: RiskEvaluationView;
  error?: unknown;
}

export function RiskVerdictPanel({ status, data, error }: RiskVerdictPanelProps) {
  const { t } = useTranslation();
  if (!isFeatureEnabled("FF_J3_RISK_PANEL")) return null;

  const reasonCodes = status === "success" && data ? data.reasonCodes : extractReasonCodes(error);

  return (
    <Card role="region" aria-label={t("riskVerdictPanel.title")}>
      <CardTitle>{t("riskVerdictPanel.title")}</CardTitle>

      {status === "idle" && <p className="text-sm text-fg-muted">{t("riskVerdictPanel.noVerdict")}</p>}

      {status === "pending" && (
        <p className="text-sm text-fg-muted" role="status">
          {t("riskVerdictPanel.pending")}
        </p>
      )}

      {status === "success" && data && (
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-fg-muted">{t("riskVerdictPanel.outcomeLabel")}</span>
            <StatusBadge status={data.outcome} />
          </div>
          {data.outcome !== "ALLOW" && (
            <div>
              <h3 className="text-xs font-medium uppercase text-fg-muted">
                {t("riskVerdictPanel.reasonCodesLabel")}
              </h3>
              {reasonCodes.length > 0 ? (
                <ul className="mt-1 list-disc pl-5 text-fg-muted">
                  {reasonCodes.map((code) => (
                    <li key={code}>{describeReasonCode(code)}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-fg-muted">{t("riskVerdictPanel.reasonCodesEmpty")}</p>
              )}
            </div>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-fg-muted">
            <dt>{t("riskVerdictPanel.ruleBasisLabel")}</dt>
            <dd>{data.ruleVersion}</dd>
            <dt>{t("riskVerdictPanel.evaluatedAtLabel")}</dt>
            <dd>{data.evaluatedAt}</dd>
          </dl>
        </div>
      )}

      {status === "error" && (
        <div className="space-y-2">
          <Alert tone="danger">{t("riskVerdictPanel.failClosed")}</Alert>
          {reasonCodes.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-fg-muted">
              {reasonCodes.map((code) => (
                <li key={code}>{describeReasonCode(code)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
