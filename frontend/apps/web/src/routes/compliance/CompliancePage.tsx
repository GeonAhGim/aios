import { useMandateStatus } from "@aios/shared-hooks";
import { LoadingState, PageHeader } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";
import { ComplianceActionError } from "./ComplianceActionError";
import { ComplianceDecisionLookupPanel } from "./ComplianceDecisionLookupPanel";
import { ComplianceDecisionPanel } from "./ComplianceDecisionPanel";
import { ComplianceMandateStatusCard } from "./ComplianceMandateStatusCard";
import { useTranslation } from "react-i18next";

// task-2620(H-2 CM-17 프론트) + task-2668(CM-18): ADR-2026-09-09-B H-2, spec
// L4_compliance_and_regulatory §9. mandate 편집·승인 흐름(CM-19)은 이 화면 범위
// 밖이라 조회 전용 카드만 둔다. ComplianceDecisionPanel(CM-17 시점, 이 파일 하단
// 소스 참조)은 policy:evaluate 위의 reasonCodes 평면 목록이고,
// ComplianceDecisionLookupPanel(CM-18)이 task-2618의 GET /decisions/{id}로 실제
// ComplianceDecision/RuleHit(규칙 히트, severity·evidence)를 렌더한다 — 두 패널은
// 서로 다른 API·다른 판정 표현이라 하나로 합치지 않는다(각자 파일 상단 주석 참조).
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
        <ComplianceDecisionLookupPanel />
      </div>
    </AppShell>
  );
}
