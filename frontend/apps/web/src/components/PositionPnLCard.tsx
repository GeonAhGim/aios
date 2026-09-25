import type { ParsedPnLBreakdown, ParsedPositionSnapshot, PositionSnapshotView } from "@aios/shared-types";
import { Alert, Badge, Card, CardTitle, Stat } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// spec §3.2 (B) PositionSnapshotView/PnLBreakdown 표시 전용 카드. 서버 라우트가
// 아직 없으므로(task-628 decision) fetch는 하지 않고, 이미 파싱된 결과를 그대로
// props로 받는다 — ReadinessChecksTable과 같은 순수 표시 컴포넌트 패턴.
//
// mark_price 부재(POS_MARK_STALE)로 unrealized_pnl_base가 null이면 "0"이 아니라
// "평가 불가"로 구분 표기한다 — 0으로 뭉개면 실제로는 미실현 손익이 0인 경우와
// 값을 낼 수 없는 경우를 화면에서 구분할 수 없게 된다.
interface PositionPnLCardProps {
  snapshot: ParsedPositionSnapshot;
  pnl?: ParsedPnLBreakdown;
}

function moneyLabel(amount: string, currency: string): string {
  return `${amount} ${currency}`;
}

function UnrealizedStat({ snapshot }: { snapshot: PositionSnapshotView }) {
  const { t } = useTranslation();
  if (snapshot.unrealized_pnl_base === null) {
    return <Stat label={t("legacy.positionPnLCard.label1")} value="평가 불가" tone="default" />;
  }
  return <Stat label={t("legacy.positionPnLCard.label2")} value={snapshot.unrealized_pnl_base} />;
}

function SnapshotBody({ snapshot }: { snapshot: PositionSnapshotView }) {
  const { t } = useTranslation();
  const isMarkStale = snapshot.mark_price === null || snapshot.mark_at === null;

  return (
    <>
      <div className="flex items-center justify-between">
        <CardTitle className="mb-0 font-mono">{snapshot.position_key}</CardTitle>
        {isMarkStale && (
          <Badge tone="warning" data-testid="mark-stale-badge">
            {t("legacy.positionPnLCard.t3")}</Badge>
        )}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label={t("legacy.positionPnLCard.label4")} value={snapshot.quantity} />
        <Stat label={t("legacy.positionPnLCard.label5")} value={moneyLabel(snapshot.avg_cost.amount, snapshot.avg_cost.currency)} />
        <Stat label={t("legacy.positionPnLCard.label6")} value={snapshot.cost_method} />
        <UnrealizedStat snapshot={snapshot} />
        <Stat label={t("legacy.positionPnLCard.label7")} value={snapshot.realized_pnl_base} />
        <Stat label={t("legacy.positionPnLCard.label8")} value={snapshot.fees_base} />
        <Stat label={t("legacy.positionPnLCard.label9")} value={snapshot.funding_base} />
        <Stat
          label={t("legacy.positionPnLCard.label10")}
          value={snapshot.mark_price ? moneyLabel(snapshot.mark_price.amount, snapshot.mark_price.currency) : "-"}
        />
      </div>
    </>
  );
}

function PnLBreakdownRow({ pnl }: { pnl: ParsedPnLBreakdown }) {
  const { t } = useTranslation();
  if (pnl.kind !== "ok") return null;
  const { realized, unrealized, fees, funding, total, base_currency } = pnl.value;
  return (
    <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-5" data-testid="pnl-breakdown">
      <Stat label={t("legacy.positionPnLCard.label11")} value={moneyLabel(realized, base_currency)} />
      <Stat label={t("legacy.positionPnLCard.label12")} value={moneyLabel(unrealized, base_currency)} />
      <Stat label={t("legacy.positionPnLCard.label13")} value={moneyLabel(fees, base_currency)} />
      <Stat label={t("legacy.positionPnLCard.label14")} value={moneyLabel(funding, base_currency)} />
      <Stat label={t("legacy.positionPnLCard.label15")} value={moneyLabel(total, base_currency)} />
    </div>
  );
}

export function PositionPnLCard({ snapshot, pnl }: PositionPnLCardProps) {
  const { t } = useTranslation();
  if (snapshot.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="position-pnl-card">
        <Alert tone="danger">{t("legacy.positionPnLCard.t16", { string: String(snapshot.received) })}</Alert>
      </Card>
    );
  }

  if (snapshot.kind !== "ok") {
    return (
      <Card data-testid="position-pnl-card">
        <Alert tone="danger">{t("legacy.positionPnLCard.t17")}</Alert>
      </Card>
    );
  }

  return (
    <Card data-testid="position-pnl-card">
      <SnapshotBody snapshot={snapshot.value} />
      {pnl && <PnLBreakdownRow pnl={pnl} />}
    </Card>
  );
}
