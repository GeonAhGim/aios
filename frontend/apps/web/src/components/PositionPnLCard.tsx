import type { ParsedPnLBreakdown, ParsedPositionSnapshot, PositionSnapshotView } from "@aios/shared-types";
import { Alert, Badge, Card, CardTitle, Stat } from "@aios/ui-web";

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
  if (snapshot.unrealized_pnl_base === null) {
    return <Stat label="미실현 손익" value="평가 불가" tone="default" />;
  }
  return <Stat label="미실현 손익" value={snapshot.unrealized_pnl_base} />;
}

function SnapshotBody({ snapshot }: { snapshot: PositionSnapshotView }) {
  const isMarkStale = snapshot.mark_price === null || snapshot.mark_at === null;

  return (
    <>
      <div className="flex items-center justify-between">
        <CardTitle className="mb-0 font-mono">{snapshot.position_key}</CardTitle>
        {isMarkStale && (
          <Badge tone="warning" data-testid="mark-stale-badge">
            마크 가격 스테일
          </Badge>
        )}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="수량" value={snapshot.quantity} />
        <Stat label="평균단가" value={moneyLabel(snapshot.avg_cost.amount, snapshot.avg_cost.currency)} />
        <Stat label="원가법" value={snapshot.cost_method} />
        <UnrealizedStat snapshot={snapshot} />
        <Stat label="실현 손익" value={snapshot.realized_pnl_base} />
        <Stat label="수수료" value={snapshot.fees_base} />
        <Stat label="펀딩" value={snapshot.funding_base} />
        <Stat
          label="마크 가격"
          value={snapshot.mark_price ? moneyLabel(snapshot.mark_price.amount, snapshot.mark_price.currency) : "-"}
        />
      </div>
    </>
  );
}

function PnLBreakdownRow({ pnl }: { pnl: ParsedPnLBreakdown }) {
  if (pnl.kind !== "ok") return null;
  const { realized, unrealized, fees, funding, total, base_currency } = pnl.value;
  return (
    <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-5" data-testid="pnl-breakdown">
      <Stat label="실현(합산)" value={moneyLabel(realized, base_currency)} />
      <Stat label="미실현(합산)" value={moneyLabel(unrealized, base_currency)} />
      <Stat label="수수료(합산)" value={moneyLabel(fees, base_currency)} />
      <Stat label="펀딩(합산)" value={moneyLabel(funding, base_currency)} />
      <Stat label="합계" value={moneyLabel(total, base_currency)} />
    </div>
  );
}

export function PositionPnLCard({ snapshot, pnl }: PositionPnLCardProps) {
  if (snapshot.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="position-pnl-card">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(snapshot.received)}).</Alert>
      </Card>
    );
  }

  if (snapshot.kind !== "ok") {
    return (
      <Card data-testid="position-pnl-card">
        <Alert tone="danger">포지션 데이터를 해석할 수 없습니다.</Alert>
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
