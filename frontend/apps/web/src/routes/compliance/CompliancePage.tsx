import { useMandateStatus } from "@aios/shared-hooks";
import { LoadingState, PageHeader } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";
import { ComplianceActionError } from "./ComplianceActionError";
import { ComplianceDecisionPanel } from "./ComplianceDecisionPanel";
import { ComplianceMandateStatusCard } from "./ComplianceMandateStatusCard";
import { useTranslation } from "react-i18next";

// task-2620(H-2 CM-17 프론트): ADR-2026-09-09-B H-2, spec L4_compliance_and_regulatory
// §9 CM-18 DoD("판정 조회·규칙 히트")를 이 화면이 담당한다. CM-17 전용 API 라우터는
// 아직 없으므로(§9 진행 현황: CM-17 inflight) 이미 배선된 CM-16 시점 API 계약
// (/v1/foundation/mandates — status·policy:evaluate)을 그대로 소비한다. mandate
// 편집·승인 흐름(CM-19)은 이 화면 범위 밖이라 조회 전용 카드만 둔다.
export function CompliancePage() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useMandateStatus();

  if (isError) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title={t("legacy.compliancePage.title1")} />
          <ComplianceActionError error={error} onRetry={() => refetch()} />
        </div>
      </AppShell>
    );
  }

  if (isLoading || !data) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title={t("legacy.compliancePage.title2")} />
          <LoadingState />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("legacy.compliancePage.title3")} />
        <ComplianceMandateStatusCard
          activeRevision={data.activeRevision}
          pendingRevision={data.pendingRevision}
        />
        <ComplianceDecisionPanel />
      </div>
    </AppShell>
  );
}
