import { EmptyState } from "@aios/ui-web";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { isFeatureEnabled, type FeatureFlagKey } from "../lib/featureFlags";

interface FeatureFlagGateProps {
  flag: FeatureFlagKey;
  children: ReactNode;
}

// task-2632(U-10): 라우트 단위로 온보딩 마법사·데모 모드를 끌 수 있어야 한다는
// 공통 DoD("기능 플래그")를 만족시키는 최소 게이트. 플래그가 꺼져 있으면 자식을
// 아예 마운트하지 않는다(내부 훅이 실행되며 부작용을 남기지 않도록).
export function FeatureFlagGate({ flag, children }: FeatureFlagGateProps) {
  const { t } = useTranslation();
  if (!isFeatureEnabled(flag)) {
    return <EmptyState>{t("common.featureDisabled")}</EmptyState>;
  }
  return <>{children}</>;
}
