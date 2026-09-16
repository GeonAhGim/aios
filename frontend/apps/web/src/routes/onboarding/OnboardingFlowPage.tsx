import { useExchangeCredentials, useMyStrategies, usePaperDeployments } from "@aios/shared-hooks";
import { Badge, Button, Card, CardTitle, LoadingState, PageHeader } from "@aios/ui-web";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { deriveOnboardingProgress, type OnboardingStepId } from "../../onboarding/onboardingProgress";

interface OnboardingStepDef {
  id: OnboardingStepId;
  to: string;
  titleKey: string;
  descriptionKey: string;
  ctaKey: string;
}

// UX-20: 거래소 연결 → 전략 생성 → 페이퍼 실행 순서로 기존 화면(라우트)을 그대로
// 재사용한다 — 온보딩 전용 입력 폼을 새로 만들지 않고, 각 단계의 진행 여부만
// 실 서버 데이터(useExchangeCredentials/useMyStrategies/usePaperDeployments)로
// 판정해 다음에 할 일을 안내한다.
const ONBOARDING_STEPS: readonly OnboardingStepDef[] = [
  {
    id: "connectExchange",
    to: "/exchanges",
    titleKey: "onboarding.steps.connectExchange.title",
    descriptionKey: "onboarding.steps.connectExchange.description",
    ctaKey: "onboarding.steps.connectExchange.cta",
  },
  {
    id: "createStrategy",
    to: "/strategy-builder",
    titleKey: "onboarding.steps.createStrategy.title",
    descriptionKey: "onboarding.steps.createStrategy.description",
    ctaKey: "onboarding.steps.createStrategy.cta",
  },
  {
    id: "runPaper",
    to: "/system/paper-deployments",
    titleKey: "onboarding.steps.runPaper.title",
    descriptionKey: "onboarding.steps.runPaper.description",
    ctaKey: "onboarding.steps.runPaper.cta",
  },
];

export function OnboardingFlowPage() {
  const { t } = useTranslation();
  const credentials = useExchangeCredentials();
  const strategies = useMyStrategies();
  const deployments = usePaperDeployments();

  const isLoading = credentials.isLoading || strategies.isLoading || deployments.isLoading;
  const progress = deriveOnboardingProgress({
    exchangeCredentials: { data: credentials.data, isError: credentials.isError },
    strategies: { data: strategies.data, isError: strategies.isError },
    paperDeployments: { data: deployments.data, isError: deployments.isError },
  });

  return (
    <AppShell>
      <div className="max-w-2xl space-y-8">
        <PageHeader title={t("onboarding.pageTitle")} />

        {isLoading ? (
          <LoadingState />
        ) : progress.isComplete ? (
          <Card>
            <CardTitle>{t("onboarding.completeTitle")}</CardTitle>
            <p className="text-sm text-fg-muted">{t("onboarding.completeDescription")}</p>
            <Link to="/dashboard" className="mt-4 inline-block">
              <Button type="button">{t("onboarding.goToDashboard")}</Button>
            </Link>
          </Card>
        ) : (
          <ol className="space-y-4">
            {ONBOARDING_STEPS.map((step, index) => {
              const status = progress.steps.find((s) => s.id === step.id);
              const complete = status?.complete ?? false;
              const isCurrent = progress.currentStepId === step.id;
              return (
                <li key={step.id}>
                  <Card>
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-hover text-sm font-semibold text-fg">
                          {index + 1}
                        </span>
                        <div>
                          <CardTitle className="mb-1">{t(step.titleKey as any)}</CardTitle>
                          <p className="text-sm text-fg-muted">{t(step.descriptionKey as any)}</p>
                        </div>
                      </div>
                      {complete ? (
                        <Badge tone="success">{t("onboarding.stepDone")}</Badge>
                      ) : isCurrent ? (
                        <Link to={step.to}>
                          <Button type="button">{t(step.ctaKey as any)}</Button>
                        </Link>
                      ) : (
                        <Badge tone="neutral">{t("onboarding.stepUpcoming")}</Badge>
                      )}
                    </div>
                  </Card>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </AppShell>
  );
}
