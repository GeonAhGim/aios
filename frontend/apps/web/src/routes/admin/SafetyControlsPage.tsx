import { useDeactivateSafetyControl, useEvaluateRecovery, useSafetyControls } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, describeReasonCode, routeApiError } from "@aios/shared-types";
import type { RecoveryDecisionView, SafetyControlView } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 해제(deactivate)·복구평가(evaluate-recovery) 실패는
// err.message를 직접 노출하지 않고 routeApiError로 판정해 403/그 외를 각각
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(DisputeManagementPage/WalletTopupsPage
// 패턴, task-901/910/911/483/1072). decision: 이 화면은 읽기·해제만 다룬다 — 개통
// (activate)·룰번들 승인/활성화·evaluate 트리거 UI는 없다(후속 리프 2336~2338 소관).
function SafetyControlActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={onRetry}
    />
  );
}

function RecoveryDecisionSummary({ decision }: { decision: RecoveryDecisionView }) {
  return (
    <div className="mt-2 rounded-md border border-border bg-surface-hover p-3 text-sm">
      <p className="font-medium text-fg">
        복구 평가 결과: <StatusBadge status={decision.outcome} />
      </p>
      {decision.reasonCodes.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-fg-muted">
          {decision.reasonCodes.map((code) => (
            <li key={code}>{describeReasonCode(code)}</li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-xs text-fg-muted">
        만료: {new Date(decision.expiresAt).toLocaleString()}
      </p>
    </div>
  );
}

export function SafetyControlsPage() {
  const { data, isLoading, isError, error, refetch } = useSafetyControls();
  const deactivate = useDeactivateSafetyControl();
  const evaluateRecovery = useEvaluateRecovery();

  const [actionError, setActionError] = useState<{ controlId: string; error: unknown } | null>(null);
  const [recoveryInputs, setRecoveryInputs] = useState<Record<string, { approvalId: string; evidenceRef: string }>>(
    {},
  );
  const [recoveryDecisions, setRecoveryDecisions] = useState<Record<string, RecoveryDecisionView>>({});

  function recoveryInput(controlId: string) {
    return recoveryInputs[controlId] ?? { approvalId: "", evidenceRef: "" };
  }

  function setRecoveryField(controlId: string, field: "approvalId" | "evidenceRef", value: string) {
    setRecoveryInputs((prev) => ({ ...prev, [controlId]: { ...recoveryInput(controlId), [field]: value } }));
  }

  function handleDeactivate(controlId: string) {
    setActionError(null);
    deactivate.mutate(controlId, { onError: (err) => setActionError({ controlId, error: err }) });
  }

  function handleEvaluateRecovery(controlId: string) {
    const input = recoveryInput(controlId);
    const approvalId = Number(input.approvalId);
    if (!input.approvalId || Number.isNaN(approvalId)) {
      setActionError({ controlId, error: new Error("승인 요청 ID를 숫자로 입력하세요.") });
      return;
    }
    setActionError(null);
    evaluateRecovery.mutate(
      { controlId, body: { approvalId, evidenceRef: input.evidenceRef || undefined } },
      {
        onSuccess: (decision) => setRecoveryDecisions((prev) => ({ ...prev, [controlId]: decision })),
        onError: (err) => setActionError({ controlId, error: err }),
      },
    );
  }

  function renderControl(control: SafetyControlView) {
    const isActive = control.state === "ACTIVE";
    return (
      <li key={control.id} className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <p className="font-medium text-fg">
                {control.scope} · {control.scopeRef}
              </p>
              <StatusBadge status={control.state} />
            </div>
            <p className="text-sm text-fg-muted">{control.reason}</p>
            <p className="text-xs text-fg-muted">
              펜스 토큰 {control.fenceToken}
              {control.createdAt && ` · 발동: ${new Date(control.createdAt).toLocaleString()}`}
              {control.deactivatedAt && ` · 해제: ${new Date(control.deactivatedAt).toLocaleString()}`}
            </p>
          </div>
          {isActive && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={deactivate.isPending}
              onClick={() => handleDeactivate(control.id)}
            >
              즉시 해제
            </Button>
          )}
        </div>
        {isActive && (
          <div className="mt-3 flex items-end gap-2">
            <Input
              type="number"
              placeholder="승인 요청 ID"
              value={recoveryInput(control.id).approvalId}
              onChange={(e) => setRecoveryField(control.id, "approvalId", e.target.value)}
              className="w-36"
            />
            <Input
              type="text"
              placeholder="증빙 참조(evidence_ref)"
              value={recoveryInput(control.id).evidenceRef}
              onChange={(e) => setRecoveryField(control.id, "evidenceRef", e.target.value)}
              className="w-56"
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={evaluateRecovery.isPending}
              onClick={() => handleEvaluateRecovery(control.id)}
            >
              복구 평가(R-53)
            </Button>
          </div>
        )}
        {recoveryDecisions[control.id] && <RecoveryDecisionSummary decision={recoveryDecisions[control.id]} />}
        {actionError?.controlId === control.id && (
          <div className="mt-3">
            <SafetyControlActionError error={actionError.error} />
          </div>
        )}
      </li>
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="안전 통제(Safety Controls)" />
        {isError ? (
          <SafetyControlActionError error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <LoadingState />
        ) : data && data.controls.length > 0 ? (
          <ul className="space-y-3">{data.controls.map(renderControl)}</ul>
        ) : (
          <EmptyState>활성 안전 통제가 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
