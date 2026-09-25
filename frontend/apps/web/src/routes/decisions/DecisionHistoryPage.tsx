import { PageHeader } from "@aios/ui-web";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import type { PositionsClientLike } from "../../hooks/usePositions";
import { ComplianceDecisionLookupPanel } from "../compliance/ComplianceDecisionLookupPanel";
import { EventLineageLookupPanel } from "./EventLineageLookupPanel";

// task-2706 UX-22: spec L4_product_experience_and_discovery_v1.0.md §9 UX-22 DoD
// ("차단 사유 역추적 재현", UX-A5). 판정(왜 막혔나)과 이벤트 계보(무슨 일이
// 있었나)는 서로 다른 API·다른 SSOT라 하나의 조회로 합치지 않는다
// (ComplianceDecisionLookupPanel CM-18과 EventLineageLookupPanel을 각자 그대로
// 두어 새 판정 로직을 만들지 않는다 — UX-A5/CM-A5와 동일 원칙). 이 페이지는 두
// 기존 조회를 한 화면에 나란히 두어 "역추적"의 진입점만 하나로 만든다.
// "차단 사유 역추적 재현"은 CM-13(explain.py) 쪽 보장이다 — 동일 decision_id로
// 다시 조회하면 서버가 동일 판정을 돌려주고(그 재현 테스트는 CM-13/CM-18
// 리프에 이미 있다), 이 페이지는 그 값을 그대로 보여줄 뿐이다.
export interface DecisionHistoryPageProps {
  positionsClient?: Pick<PositionsClientLike, "getPositionJournal">;
}

export function DecisionHistoryPage({ positionsClient }: DecisionHistoryPageProps = {}) {
  const { t } = useTranslation();
  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("decisions.pageTitle")} />
        <ComplianceDecisionLookupPanel />
        <EventLineageLookupPanel client={positionsClient} />
      </div>
    </AppShell>
  );
}
