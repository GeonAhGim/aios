import { ApiError } from "@aios/api-client";
import { useBeginConnection } from "@aios/shared-hooks";
import { classifyForbidden, routeApiError, type CapabilityScope } from "@aios/shared-types";
import { Alert, Button, Field, Input, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

const CAPABILITY_OPTIONS: CapabilityScope[] = ["READ_BALANCE", "READ_POSITION", "READ_ACTIVITY"];
type WizardStep = "provider" | "permissions" | "review";
const STEP_ORDER: WizardStep[] = ["provider", "permissions", "review"];

// ConnectionsPage.tsx의 ConnectionActionError와 동일 3-way 판정(classifyForbidden/
// routeApiError → ForbiddenNotice/ErrorMessage) — decision: 새 에러 분류기를
// 신설하지 않고 그대로 재사용한다.
function WizardActionError({ error }: { error: unknown }) {
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

// 키 원문을 화면 어디에도 남기지 않는다(spec: "키는 화면에서 마스킹·서버 KeyRing
// 저장만") — 끝 4자만 보이고 나머지는 길이와 무관하게 고정 6개 점으로 가린다
// (길이 자체도 노출하지 않기 위해 실제 길이에 비례시키지 않는다).
function maskKey(value: string): string {
  if (value.length <= 4) return "•".repeat(value.length);
  return `${"•".repeat(6)}${value.slice(-4)}`;
}

export function ConnectionWizardPage() {
  const { t } = useTranslation();
  const beginConnection = useBeginConnection();

  const [step, setStep] = useState<WizardStep>("provider");
  const [providerCode, setProviderCode] = useState("");
  const [opaqueAccountRef, setOpaqueAccountRef] = useState("");
  const [capabilityProfile, setCapabilityProfile] = useState<CapabilityScope[]>([]);
  const [readonlyConfirmed, setReadonlyConfirmed] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [succeeded, setSucceeded] = useState(false);

  function toggleCapability(scope: CapabilityScope) {
    setCapabilityProfile((prev) => (prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]));
  }

  const providerStepValid = providerCode.trim().length > 0 && opaqueAccountRef.trim().length > 0 && capabilityProfile.length > 0;

  function handleSubmit() {
    setSubmitError(null);
    beginConnection.mutate(
      {
        providerCode: providerCode.trim(),
        opaqueAccountRef: opaqueAccountRef.trim(),
        requestedCapabilityProfile: capabilityProfile,
      },
      {
        onSuccess: () => setSucceeded(true),
        onError: (err) => setSubmitError(err),
      },
    );
  }

  const stepIndex = STEP_ORDER.indexOf(step) + 1;

  return (
    <AppShell>
      <div className="max-w-xl space-y-6">
        <PageHeader title={t("connectWizard.pageTitle")} />
        {!succeeded && (
          <p className="text-xs text-fg-muted" data-testid="wizard-step-indicator">
            {t("connectWizard.stepLabel", { step: stepIndex, total: STEP_ORDER.length })}
          </p>
        )}

        {succeeded ? (
          <Alert tone="success">
            <p>{t("connectWizard.steps.review.success")}</p>
            <Link to="/settings/connections" className="mt-3 inline-block">
              <Button type="button" size="sm">
                {t("connectWizard.steps.review.goToConnections")}
              </Button>
            </Link>
          </Alert>
        ) : (
          <>
            {step === "provider" && (
              <section className="space-y-4">
                <h2 className="text-sm font-semibold text-fg">{t("connectWizard.steps.provider.title")}</h2>
                <Field label={t("connectWizard.steps.provider.providerLabel")}>
                  <Input value={providerCode} onChange={(e) => setProviderCode(e.target.value)} className="w-full" />
                </Field>
                <Field label={t("connectWizard.steps.provider.keyLabel")} hint={t("connectWizard.steps.provider.keyHint")}>
                  <Input
                    type="password"
                    value={opaqueAccountRef}
                    onChange={(e) => setOpaqueAccountRef(e.target.value)}
                    placeholder={t("connectWizard.steps.provider.keyPlaceholder")}
                    className="w-full"
                    autoComplete="off"
                  />
                </Field>
                <fieldset className="space-y-1.5">
                  <legend className="text-sm font-medium text-fg-secondary">
                    {t("connectWizard.steps.provider.capabilityLegend")}
                  </legend>
                  <div className="flex gap-4">
                    {CAPABILITY_OPTIONS.map((scope) => (
                      <label key={scope} className="flex items-center gap-1.5 text-sm text-fg">
                        <input
                          type="checkbox"
                          aria-label={scope}
                          className="accent-accent"
                          checked={capabilityProfile.includes(scope)}
                          onChange={() => toggleCapability(scope)}
                        />
                        {scope}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <Button type="button" disabled={!providerStepValid} onClick={() => setStep("permissions")}>
                  {t("connectWizard.steps.provider.next")}
                </Button>
              </section>
            )}

            {step === "permissions" && (
              <section className="space-y-4">
                <h2 className="text-sm font-semibold text-fg">{t("connectWizard.steps.permissions.title")}</h2>
                <Alert tone="warning">
                  <p>{t("connectWizard.steps.permissions.readonlyNotice")}</p>
                  <ul className="mt-2 list-inside list-disc text-sm">
                    {capabilityProfile.map((scope) => (
                      <li key={scope}>{scope}</li>
                    ))}
                  </ul>
                </Alert>
                <label className="flex items-center gap-2 text-sm text-fg">
                  <input
                    type="checkbox"
                    aria-label={t("connectWizard.steps.permissions.confirmLabel")}
                    className="accent-accent"
                    checked={readonlyConfirmed}
                    onChange={(e) => setReadonlyConfirmed(e.target.checked)}
                  />
                  {t("connectWizard.steps.permissions.confirmLabel")}
                </label>
                <div className="flex gap-2">
                  <Button type="button" variant="secondary" onClick={() => setStep("provider")}>
                    {t("connectWizard.steps.permissions.back")}
                  </Button>
                  <Button type="button" disabled={!readonlyConfirmed} onClick={() => setStep("review")}>
                    {t("connectWizard.steps.permissions.next")}
                  </Button>
                </div>
              </section>
            )}

            {step === "review" && (
              <section className="space-y-4">
                <h2 className="text-sm font-semibold text-fg">{t("connectWizard.steps.review.title")}</h2>
                <dl className="space-y-2 rounded-lg border border-border bg-surface p-4 text-sm">
                  <div className="flex justify-between">
                    <dt className="text-fg-muted">{t("connectWizard.steps.review.providerLabel")}</dt>
                    <dd className="text-fg">{providerCode}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-fg-muted">{t("connectWizard.steps.review.keyLabel")}</dt>
                    <dd className="text-fg" data-testid="wizard-masked-key">
                      {maskKey(opaqueAccountRef)}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-fg-muted">{t("connectWizard.steps.review.capabilityLabel")}</dt>
                    <dd className="text-fg">{capabilityProfile.join(", ")}</dd>
                  </div>
                </dl>
                {submitError ? <WizardActionError error={submitError} /> : null}
                <div className="flex gap-2">
                  <Button type="button" variant="secondary" onClick={() => setStep("permissions")}>
                    {t("connectWizard.steps.review.back")}
                  </Button>
                  <Button type="button" loading={beginConnection.isPending} onClick={handleSubmit}>
                    {t("connectWizard.steps.review.submit")}
                  </Button>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
