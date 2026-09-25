import {
  useActivateRuleBundle,
  useActivateSafetyControl,
  useApproveRuleBundle,
  useDeactivateSafetyControl,
  useEvaluateRecovery,
  useEvaluateRiskGate,
  useSafetyControls,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, describeReasonCode, routeApiError } from "@aios/shared-types";
import type {
  GateKind,
  RecoveryDecisionView,
  RiskEvaluationView,
  RiskRuleBundle,
  SafetyControlView,
  SafetyScope,
} from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader, Select, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useTranslation } from "react-i18next";

const SAFETY_SCOPES: SafetyScope[] = ["GLOBAL", "TENANT", "ACCOUNT", "STRATEGY_DEPLOYMENT", "PROVIDER"];
const GATE_KINDS: GateKind[] = ["DEPLOYMENT", "PRE_INTENT", "PRE_TRADE", "PRE_SUBMIT", "INTRADAY", "RECOVERY"];

// spec §3.3 에러 taxonomy: 해제(deactivate)·복구평가(evaluate-recovery)·개통(activate)·
// 룰번들 승인/활성화·evaluate 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage 경로로만 보여준다
// (DisputeManagementPage/WalletTopupsPage 패턴, task-901/910/911/483/1072/5810).
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
  const { t } = useTranslation();
  return (
    <div className="mt-2 rounded-md border border-border bg-surface-hover p-3 text-sm">
      <p className="font-medium text-fg">
        {t("legacy.safetyControlsPage.t1")}<StatusBadge status={decision.outcome} />
      </p>
      {decision.reasonCodes.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-fg-muted">
          {decision.reasonCodes.map((code) => (
            <li key={code}>{describeReasonCode(code)}</li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-xs text-fg-muted">
        {t("legacy.safetyControlsPage.t2")}{new Date(decision.expiresAt).toLocaleString()}
      </p>
    </div>
  );
}

function RiskEvaluationSummary({ evaluation }: { evaluation: RiskEvaluationView }) {
  const { t } = useTranslation();
  return (
    <div className="mt-2 rounded-md border border-border bg-surface-hover p-3 text-sm">
      <p className="font-medium text-fg">
        {t("legacy.safetyControlsPage.evaluateResultPrefix")} <StatusBadge status={evaluation.outcome} />
      </p>
      {evaluation.reasonCodes.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-fg-muted">
          {evaluation.reasonCodes.map((code) => (
            <li key={code}>{describeReasonCode(code)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RuleBundleSummary({ bundle }: { bundle: RiskRuleBundle }) {
  const { t } = useTranslation();
  return (
    <div className="mt-2 rounded-md border border-border bg-surface-hover p-3 text-sm">
      <p className="font-medium text-fg">
        {t("legacy.safetyControlsPage.ruleBundleResultPrefix")} <StatusBadge status={bundle.state} />
      </p>
    </div>
  );
}

function ActivateSafetyControlPanel({ onError }: { onError: (error: unknown) => void }) {
  const { t } = useTranslation();
  const activate = useActivateSafetyControl();
  const [scope, setScope] = useState<SafetyScope>("GLOBAL");
  const [scopeRef, setScopeRef] = useState("");
  const [reason, setReason] = useState("");

  function handleActivate() {
    onError(null);
    activate.mutate(
      { scope, scopeRef: scopeRef || undefined, reason },
      { onError: (err) => onError(err) },
    );
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="font-medium text-fg">{t("legacy.safetyControlsPage.activateHeading")}</h2>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Select value={scope} onChange={(e) => setScope(e.target.value as SafetyScope)} className="w-48">
          {SAFETY_SCOPES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Input
          type="text"
          placeholder={t("legacy.safetyControlsPage.activateScopeRefPlaceholder")}
          value={scopeRef}
          onChange={(e) => setScopeRef(e.target.value)}
          className="w-48"
        />
        <Input
          type="text"
          placeholder={t("legacy.safetyControlsPage.activateReasonPlaceholder")}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="w-56"
        />
        <Button type="button" variant="secondary" size="sm" loading={activate.isPending} onClick={handleActivate}>
          {t("legacy.safetyControlsPage.activateSubmit")}
        </Button>
      </div>
    </section>
  );
}

function EvaluateRiskGatePanel({ onError }: { onError: (error: unknown) => void }) {
  const { t } = useTranslation();
  const evaluate = useEvaluateRiskGate();
  const [gateKind, setGateKind] = useState<GateKind>("PRE_TRADE");
  const [connectionId, setConnectionId] = useState("");
  const [result, setResult] = useState<RiskEvaluationView | null>(null);

  function handleEvaluate() {
    onError(null);
    evaluate.mutate(
      { gateKind, connectionId: connectionId || undefined },
      { onSuccess: (view) => setResult(view), onError: (err) => onError(err) },
    );
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="font-medium text-fg">{t("legacy.safetyControlsPage.evaluateHeading")}</h2>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Select value={gateKind} onChange={(e) => setGateKind(e.target.value as GateKind)} className="w-48">
          {GATE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
        <Input
          type="text"
          placeholder={t("legacy.safetyControlsPage.evaluateConnectionIdPlaceholder")}
          value={connectionId}
          onChange={(e) => setConnectionId(e.target.value)}
          className="w-56"
        />
        <Button type="button" variant="secondary" size="sm" loading={evaluate.isPending} onClick={handleEvaluate}>
          {t("legacy.safetyControlsPage.evaluateSubmit")}
        </Button>
      </div>
      {result && <RiskEvaluationSummary evaluation={result} />}
    </section>
  );
}

function RuleBundlePanel({ onError }: { onError: (error: unknown) => void }) {
  const { t } = useTranslation();
  const approve = useApproveRuleBundle();
  const activate = useActivateRuleBundle();
  const [bundleId, setBundleId] = useState("");
  const [approvalRef, setApprovalRef] = useState("");
  const [bundle, setBundle] = useState<RiskRuleBundle | null>(null);

  function handleApprove() {
    onError(null);
    approve.mutate(
      { bundleId, body: { approvalRef } },
      { onSuccess: (view) => setBundle(view), onError: (err) => onError(err) },
    );
  }

  function handleActivate() {
    onError(null);
    activate.mutate(bundleId, {
      onSuccess: (view) => setBundle(view),
      onError: (err) => onError(err),
    });
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="font-medium text-fg">{t("legacy.safetyControlsPage.ruleBundleHeading")}</h2>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Input
          type="text"
          placeholder={t("legacy.safetyControlsPage.ruleBundleIdPlaceholder")}
          value={bundleId}
          onChange={(e) => setBundleId(e.target.value)}
          className="w-48"
        />
        <Input
          type="text"
          placeholder={t("legacy.safetyControlsPage.ruleBundleApprovalRefPlaceholder")}
          value={approvalRef}
          onChange={(e) => setApprovalRef(e.target.value)}
          className="w-56"
        />
        <Button type="button" variant="secondary" size="sm" loading={approve.isPending} onClick={handleApprove}>
          {t("legacy.safetyControlsPage.ruleBundleApprove")}
        </Button>
        <Button type="button" variant="secondary" size="sm" loading={activate.isPending} onClick={handleActivate}>
          {t("legacy.safetyControlsPage.ruleBundleActivate")}
        </Button>
      </div>
      {bundle && <RuleBundleSummary bundle={bundle} />}
    </section>
  );
}

export function SafetyControlsPage() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useSafetyControls();
  const deactivate = useDeactivateSafetyControl();
  const evaluateRecovery = useEvaluateRecovery();

  const [actionError, setActionError] = useState<{ controlId: string; error: unknown } | null>(null);
  const [adminActionError, setAdminActionError] = useState<unknown>(null);
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
              {t("legacy.safetyControlsPage.t3", { fenceToken: control.fenceToken })}
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
              {t("legacy.safetyControlsPage.t4")}</Button>
          )}
        </div>
        {isActive && (
          <div className="mt-3 flex items-end gap-2">
            <Input
              type="number"
              placeholder={t("legacy.safetyControlsPage.placeholder5")}
              value={recoveryInput(control.id).approvalId}
              onChange={(e) => setRecoveryField(control.id, "approvalId", e.target.value)}
              className="w-36"
            />
            <Input
              type="text"
              placeholder={t("legacy.safetyControlsPage.placeholder6")}
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
              {t("legacy.safetyControlsPage.t7")}</Button>
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
        <PageHeader title={t("legacy.safetyControlsPage.title8")} />
        <ActivateSafetyControlPanel onError={setAdminActionError} />
        <EvaluateRiskGatePanel onError={setAdminActionError} />
        <RuleBundlePanel onError={setAdminActionError} />
        {adminActionError !== null && <SafetyControlActionError error={adminActionError} />}
        {isError ? (
          <SafetyControlActionError error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <LoadingState />
        ) : data && data.controls.length > 0 ? (
          <ul className="space-y-3">{data.controls.map(renderControl)}</ul>
        ) : (
          <EmptyState>{t("legacy.safetyControlsPage.t9")}</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
