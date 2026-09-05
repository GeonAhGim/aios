import { deriveFreshness } from "@aios/api-client";
import type {
  ParsedNavSnapshot,
  ParsedPnLBreakdown,
  ParsedPositionSnapshot,
  PositionSnapshotView,
} from "@aios/shared-types";
import { Alert, Card, CardTitle, EmptyState, Stat } from "@aios/ui-web";
import type { ReactNode } from "react";
import { PositionPnLCard } from "../../components/PositionPnLCard";

// spec §3.2 (B) 포지션 스냅샷·PnL 분해·NAV를 포트폴리오 화면에 그리는 표시 전용
// 섹션. task-1524(LB-19)부터 fetch·파싱은 PortfolioPositionsLive(usePositions +
// api-client positions.ts, parsePositionSnapshot/parseNavSnapshot 재사용)가 맡고, 이
// 컴포넌트는 이미 판별된 결과(Parsed*)만 받아 성공/실패 갈래를 그린다 — 여기서
// 파서를 재구현하지 않는다. 낙관적 갱신은 하지 않는다(SSOT=서버, task-709 decision).
// PnL 분해(PnLBreakdown)는 LB-19에 조회 라우트가 없어 pnl prop을 넘기는 호출부가
// 아직 없다 — 라우트가 생기면 같은 prop으로 연결한다.
const STALE_AFTER_SEC = 300;

interface PortfolioPositionsSectionProps {
  positions: ParsedPositionSnapshot[];
  pnl?: ParsedPnLBreakdown;
  nav?: ParsedNavSnapshot;
  /** 봉투 meta.as_of. 없으면 신선도 판정 불가(배너 없음) — Date.now() 대입 금지(task-936). */
  asOf?: string | null;
  now?: Date;
  /** 정상 파싱된 포지션 카드 아래에 붙일 부가 영역(저널 패널 등). */
  renderPositionExtra?: (snapshot: PositionSnapshotView) => ReactNode;
}

function NavCard({ nav }: { nav: ParsedNavSnapshot }) {
  if (nav.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(nav.received)}).</Alert>
      </Card>
    );
  }
  if (nav.kind !== "ok") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">NAV 데이터를 해석할 수 없습니다.</Alert>
      </Card>
    );
  }
  const snapshot = nav.value;
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

function PnLBreakdownCard({ pnl }: { pnl: ParsedPnLBreakdown }) {
  if (pnl.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">지원하지 않는 schema_version입니다 ({String(pnl.received)}).</Alert>
      </Card>
    );
  }
  if (pnl.kind !== "ok") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">PnL 분해 데이터를 해석할 수 없습니다.</Alert>
      </Card>
    );
  }
  const { realized, unrealized, fees, funding, total, base_currency } = pnl.value;
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
  renderPositionExtra,
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
            {positions.map((parsed, index) => {
              const key = parsed.kind === "ok" ? parsed.value.position_key : `invalid-${index}`;
              return (
                <div key={key} className="space-y-2">
                  <PositionPnLCard snapshot={parsed} />
                  {parsed.kind === "ok" && renderPositionExtra?.(parsed.value)}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
