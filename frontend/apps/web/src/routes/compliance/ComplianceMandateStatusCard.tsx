import type { MandateRevisionView } from "@aios/shared-types";
import { Badge, Card, StatusBadge } from "@aios/ui-web";

// task-2620(H-2 CM-17 프론트): 컴플라이언스 화면의 mandate 상태는 조회 전용이다 —
// 편집·승인(pause/resume/activate)은 CM-19(MandatePage, 별도 리프)의 몫이므로 여기서는
// 액션 버튼을 두지 않는다. fail-closed 표기(MandatesPage DoD a와 동일 결정)는 그대로
// 지킨다: activeRevision이 null이면 "제한 없음"이 아니라 위임장이 없어 주문이
// 차단된다는 사실을 보여준다.
function RevisionSummary({ revision }: { revision: MandateRevisionView }) {
  return (
    <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-fg-muted">
      <li>총 노출 한도: {revision.maxTotalExposurePct}%</li>
      <li>단일 종목 한도: {revision.maxSingleInstrumentPct}%</li>
      <li>최소 현금 버퍼: {revision.minCashBufferPct}%</li>
      <li>일일 손실 한도: {revision.maxDailyLossPct}%</li>
      <li>허용 자율성: {revision.allowedAutonomy}</li>
      <li>금지 자산: {revision.forbiddenAssets.length > 0 ? revision.forbiddenAssets.join(", ") : "없음"}</li>
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
  return (
    <Card>
      <div className="flex items-center gap-2">
        <h2 className="font-medium text-fg">mandate 상태</h2>
        {activeRevision ? (
          <StatusBadge status={activeRevision.state} />
        ) : (
          <Badge tone="warning">위임장 미설정(주문 차단)</Badge>
        )}
      </div>
      {activeRevision ? (
        <RevisionSummary revision={activeRevision} />
      ) : (
        <p className="mt-2 text-sm text-fg-muted">
          활성 위임장이 없습니다 — 규칙이 없다는 뜻이 아니라 모든 주문이 차단된다는 뜻입니다.
        </p>
      )}
      {pendingRevision && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-fg">대기 중 개정안</h3>
            <StatusBadge status={pendingRevision.state} />
          </div>
          <RevisionSummary revision={pendingRevision} />
        </div>
      )}
    </Card>
  );
}
