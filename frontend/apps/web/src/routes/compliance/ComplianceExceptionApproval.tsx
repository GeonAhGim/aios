import { Alert, Button, Card, EmptyState, Field, StatusBadge, Textarea } from "@aios/ui-web";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ComplianceActionError } from "./ComplianceActionError";

// task-5996(CM-18): CompliancePage(ComplianceDecisionLookupPanel 상단 주석 참조)는
// 규칙 위반 건에 대한 "예외 승인(override)" UI가 전무했다. spec §3의 예외 승인은
// I-11(확인이 필요한 작업은 1회성 서버측 토큰으로 미리보기·실행을 연결, 클라이언트
// 플래그만으로 불가)을 요구하지만, CM_EXCEPTION_REQUIRED 토큰 발급·소비 엔드포인트가
// 아직 어떤 라우터에도 없다(ComplianceDecisionLookupPanel 상단 주석의 grep 확인과
// 동일 근거). decision(task-5996): 이번 리프는 UI 골격만 만든다 — 승인/거부 폼은
// 오직 `onSubmit`(미래의 서버 뮤테이션)을 통해서만 항목을 처리됨으로 표시하고,
// `onSubmit`이 배선되지 않은 한(현재 CompliancePage의 실제 사용처) 전체 폼을
// 명시적으로 비활성화하고 안내 문구를 보여준다 — 클라이언트 상태만으로 "승인됨"을
// 자칭하지 않는다(I-11 위반 방지).
export interface PendingComplianceException {
  id: string;
  ruleId: string;
  message: string;
}

export type ComplianceExceptionDecisionAction = "APPROVE" | "REJECT";

export interface ComplianceExceptionDecisionInput {
  exceptionId: string;
  action: ComplianceExceptionDecisionAction;
  reason: string;
}

interface ItemState {
  reason: string;
  pendingAction: ComplianceExceptionDecisionAction | null;
  error: unknown;
  resolvedAs: ComplianceExceptionDecisionAction | null;
}

function initialItemState(): ItemState {
  return { reason: "", pendingAction: null, error: null, resolvedAs: null };
}

export function ComplianceExceptionApproval({
  pendingExceptions,
  onSubmit,
}: {
  pendingExceptions: PendingComplianceException[];
  onSubmit?: (input: ComplianceExceptionDecisionInput) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [itemStates, setItemStates] = useState<Record<string, ItemState>>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  function stateOf(id: string): ItemState {
    return itemStates[id] ?? initialItemState();
  }

  function patchItem(id: string, patch: Partial<ItemState>) {
    setItemStates((prev) => ({ ...prev, [id]: { ...stateOf(id), ...patch } }));
  }

  function handleDecide(exceptionId: string, action: ComplianceExceptionDecisionAction) {
    const reason = stateOf(exceptionId).reason.trim();
    if (reason === "") {
      setValidationErrors((prev) => ({ ...prev, [exceptionId]: t("legacy.complianceExceptionApproval.t8") }));
      return;
    }
    setValidationErrors((prev) => ({ ...prev, [exceptionId]: "" }));
    patchItem(exceptionId, { pendingAction: action, error: null });

    onSubmit?.({ exceptionId, action, reason })
      .then(() => patchItem(exceptionId, { pendingAction: null, resolvedAs: action }))
      .catch((error: unknown) => patchItem(exceptionId, { pendingAction: null, error }));
  }

  if (pendingExceptions.length === 0) {
    return (
      <Card>
        <h2 className="font-medium text-fg">{t("legacy.complianceExceptionApproval.t1")}</h2>
        <div className="mt-3">
          <EmptyState>{t("legacy.complianceExceptionApproval.t2")}</EmptyState>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="font-medium text-fg">{t("legacy.complianceExceptionApproval.t1")}</h2>

      {!onSubmit && (
        <div className="mt-3">
          <Alert tone="warning">{t("legacy.complianceExceptionApproval.t3")}</Alert>
        </div>
      )}

      <ul className="mt-3 space-y-3">
        {pendingExceptions.map((item) => {
          const state = stateOf(item.id);
          const disabled = !onSubmit || state.pendingAction !== null || state.resolvedAs !== null;
          return (
            <li key={item.id} className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-fg-muted">{item.ruleId}</span>
                {state.resolvedAs && <StatusBadge status={state.resolvedAs} />}
              </div>
              <p className="mt-1 text-sm text-fg-muted">{item.message}</p>

              {!state.resolvedAs && (
                <div className="mt-2 space-y-2">
                  <Field label={t("legacy.complianceExceptionApproval.label4")} error={validationErrors[item.id] || null}>
                    <Textarea
                      value={state.reason}
                      disabled={!onSubmit}
                      onChange={(e) => patchItem(item.id, { reason: e.target.value })}
                    />
                  </Field>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="primary"
                      size="sm"
                      disabled={disabled}
                      loading={state.pendingAction === "APPROVE"}
                      onClick={() => handleDecide(item.id, "APPROVE")}
                    >
                      {t("legacy.complianceExceptionApproval.t5")}
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      disabled={disabled}
                      loading={state.pendingAction === "REJECT"}
                      onClick={() => handleDecide(item.id, "REJECT")}
                    >
                      {t("legacy.complianceExceptionApproval.t6")}
                    </Button>
                  </div>
                </div>
              )}

              {state.error !== null && (
                <div className="mt-2">
                  <ComplianceActionError error={state.error} onRetry={() => patchItem(item.id, { error: null })} />
                </div>
              )}

              {state.resolvedAs && (
                <p className="mt-2 text-xs text-fg-muted">{t("legacy.complianceExceptionApproval.t7")}</p>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
