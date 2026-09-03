import { deriveFreshness } from "@aios/api-client";
import { parseNavSnapshot, parsePnLBreakdown, parsePositionSnapshot } from "@aios/shared-types";
import { Alert, Card, CardTitle, EmptyState, Stat } from "@aios/ui-web";
import { PositionPnLCard } from "../../components/PositionPnLCard";

// spec §3.2 (B) 포지션 스냅샷·PnL 분해·NAV를 포트폴리오 화면에 배선한다. 서버
// 라우트가 아직 없으므로(task-628 decision, PositionPnLCard와 동일 원칙)
// fetch는 하지 않고 이미 받아온 raw 데이터를 props로 받아 파싱만 담당한다.
// PortfolioPage는 실 데이터 연결 전까지 빈 배열/undefined로 이 섹션을
// 렌더링해 파싱·표시 경로만 미리 배선해둔다 — 낙관적 갱신은 하지 않는다
// (포지션·PnL의 SSOT는 서버, task-709 decision).
const STALE_AFTER_SEC = 300;

interface PortfolioPositionsSectionProps {
  positions: unknown[];
  pnl?: unknown;
  nav?: unknown;
  asOf?: string | null;
  now?: Date;
}

function NavCard({ nav }: { nav: unknown }) {
  const parsed = parseNavSnapshot(nav);
  if (parsed.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(parsed.received)}).</Alert>
      </Card>
    );
  }
  if (parsed.kind !== "ok") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">NAV 데이터를 해석할 수 없습니다.</Alert>
      </Card>
    );
  }
  const snapshot = parsed.value;
  return (
    <Card data-testid="nav-snapshot-card">
      <CardTitle>NAV · {snapshot.nav_date}</CardTitle>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="기초 NAV" value={`${snapshot.opening_nav} ${snapshot.base_currency}`} />
        <Stat label="현금" value={`${snapshot.cash} ${snapshot.base_currency}`} />
        <Stat label="포지션 시가" value={`${snapshot.positions_mv} ${snapshot.base_currency}`} />
        <Stat label="기말 NAV" value={`${snapshot.closing_nav} ${snapshot.base_currency}`} />
      </div>
    </Card>
  );
}

function PnLBreakdownCard({ pnl }: { pnl: unknown }) {
  const parsed = parsePnLBreakdown(pnl);
  if (parsed.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(parsed.received)}).</Alert>
      </Card>
    );
  }
  if (parsed.kind !== "ok") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">PnL 분해 데이터를 해석할 수 없습니다.</Alert>
      </Card>
    );
  }
  const { realized, unrealized, fees, funding, total, base_currency } = parsed.value;
  return (
    <Card data-testid="pnl-breakdown-card">
      <CardTitle>PnL 분해</CardTitle>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <Stat label="실현" value={`${realized} ${base_currency}`} />
        <Stat label="미실현" value={`${unrealized} ${base_currency}`} />
        <Stat label="수수료" value={`${fees} ${base_currency}`} />
        <Stat label="펀딩" value={`${funding} ${base_currency}`} />
        <Stat label="합계" value={`${total} ${base_currency}`} />
      </div>
    </Card>
  );
}

export function PortfolioPositionsSection({
  positions,
  pnl,
  nav,
  asOf = null,
  now,
}: PortfolioPositionsSectionProps) {
  const freshness = deriveFreshness(asOf, now ?? new Date(), { staleAfterSec: STALE_AFTER_SEC });
  const isStale = freshness.kind === "ok" && freshness.isStale;

  return (
    <div className="space-y-4" data-testid="portfolio-positions-section">
      {isStale && (
        <div data-testid="positions-stale-banner">
          <Alert tone="warning">
            포지션 데이터의 기준 시각이 오래되었습니다. 최신 상태가 아닐 수 있습니다.
          </Alert>
        </div>
      )}

      {nav !== undefined && <NavCard nav={nav} />}
      {pnl !== undefined && <PnLBreakdownCard pnl={pnl} />}

      <Card>
        <CardTitle>포지션 스냅샷</CardTitle>
        {positions.length === 0 ? (
          <EmptyState>포지션 스냅샷이 없습니다.</EmptyState>
        ) : (
          <div className="space-y-3">
            {positions.map((raw, index) => {
              const parsed = parsePositionSnapshot(raw);
              const key = parsed.kind === "ok" ? parsed.value.position_key : `invalid-${index}`;
              return <PositionPnLCard key={key} snapshot={parsed} />;
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
