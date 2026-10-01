import { useExchangeCredentials, useMyStrategies, usePaperDeployments } from "@aios/shared-hooks";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { deriveOnboardingProgress, ONBOARDING_STEP_ORDER } from "./onboardingProgress";

// F-4(task-10642, UX_JOURNEYS.md §6 J1 사용감 소견): /exchanges 등 온보딩 체크리스트가
// 재사용하는 하위 화면에는 "온보딩 몇 단계 중 몇 단계인지"가 전혀 남지 않아, 완료
// 여부를 보려면 사용자가 매번 /onboarding/first-run으로 직접 돌아가야 했다(갭 G-11과
// 동일 근본 원인). 이 위젯은 OnboardingFlowPage.tsx와 같은 진행 판정 로직
// (deriveOnboardingProgress)을 재사용해 하위 화면 상단에 "N/3단계" 배지 + 체크리스트
// 링크만 노출한다 — 온보딩 단계 수 자체를 늘리지 않는다.
export function OnboardingProgressWidget() {
  const { t } = useTranslation();
  const credentials = useExchangeCredentials();
  const strategies = useMyStrategies();
  const deployments = usePaperDeployments();

  const isLoading = credentials.isLoading || strategies.isLoading || deployments.isLoading;
  if (isLoading) return null;

  const progress = deriveOnboardingProgress({
    exchangeCredentials: { data: credentials.data, isError: credentials.isError },
    strategies: { data: strategies.data, isError: strategies.isError },
    paperDeployments: { data: deployments.data, isError: deployments.isError },
  });

  if (progress.isComplete) return null;

  return (
    <Link
      to="/onboarding/first-run"
      className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-hover px-3 py-1 text-sm text-fg-muted hover:underline"
    >
      {t("onboarding.progressWidget", {
        completed: progress.completedCount,
        total: ONBOARDING_STEP_ORDER.length,
      })}
      <span className="text-accent-hover">{t("onboarding.progressWidgetLink")}</span>
    </Link>
  );
}
