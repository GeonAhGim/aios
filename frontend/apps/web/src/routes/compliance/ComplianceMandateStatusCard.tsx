import type { MandateRevisionView } from "@aios/shared-types";
import { Badge, Card, StatusBadge } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// task-2620(H-2 CM-17 프론트): 컴플라이언스 화면의 mandate 상태는 조회 전용이다 —
// 편집·승인(pause/resume/activate)은 CM-19(MandatePage, 별도 리프)의 몫이므로 여기서는
// 액션 버튼을 두지 않는다. fail-closed 표기(MandatesPage DoD a와 동일 결정)는 그대로
// 지킨다: activeRevision이 null이면 "제한 없음"이 아니라 위임장이 없어 주문이
// 차단된다는 사실을 보여준다.
function RevisionSummary({ revision }: { revision: MandateRevisionView }) {
  const { t } = useTranslation();
  return (
    <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-fg-muted">
      <li>{t("legacy.complianceMandateStatusCard.t1", { maxTotalExposurePct: revision.maxTotalExposurePct })}</li>
      <li>{t("legacy.complianceMandateStatusCard.t2", { maxSingleInstrumentPct: revision.maxSingleInstrumentPct })}</li>
      <li>{t("legacy.complianceMandateStatusCard.t3", { minCashBufferPct: revision.minCashBufferPct })}</li>
      <li>{t("legacy.complianceMandateStatusCard.t4", { maxDailyLossPct: revision.maxDailyLossPct })}</li>
      <li>{t("legacy.complianceMandateStatusCard.t5", { allowedAutonomy: revision.allowedAutonomy })}</li>
      <li>{t("legacy.complianceMandateStatusCard.t6")}{revision.forbiddenAssets.length > 0 ? revision.forbiddenAssets.join(", ") : "없음"}</li>
    </ul>
  );
}

export function ComplianceMandateStatusCard({
  activeRevision,
  pendingRevision,
}: {
  activeRevision: MandateRevisionView | null;
  pendingRevision: MandateRevisionView | null;
}) {
  const { t } = useTranslation();
  return (
    <Card>
      <div className="flex items-center gap-2">
        <h2 className="font-medium text-fg">{t("legacy.complianceMandateStatusCard.t7")}</h2>
        {activeRevision ? (
          <StatusBadge status={activeRevision.state} />
        ) : (
          <Badge tone="warning">{t("legacy.complianceMandateStatusCard.t8")}</Badge>
        )}
      </div>
      {activeRevision ? (
        <RevisionSummary revision={activeRevision} />
      ) : (
        <p className="mt-2 text-sm text-fg-muted">
          {t("legacy.complianceMandateStatusCard.t9")}</p>
      )}
      {pendingRevision && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-fg">{t("legacy.complianceMandateStatusCard.t10")}</h3>
            <StatusBadge status={pendingRevision.state} />
          </div>
          <RevisionSummary revision={pendingRevision} />
        </div>
      )}
    </Card>
  );
}
