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
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();
  if (nav.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">{t("legacy.portfolioPositionsSection.t1", { string: String(nav.received) })}</Alert>
      </Card>
    );
  }
  if (nav.kind !== "ok") {
    return (
      <Card data-testid="nav-snapshot-error">
        <Alert tone="danger">{t("legacy.portfolioPositionsSection.t2")}</Alert>
      </Card>
    );
  }
  const snapshot = nav.value;
  return (
    <Card data-testid="nav-snapshot-card">
      <CardTitle>NAV · {snapshot.nav_date}</CardTitle>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label={t("legacy.portfolioPositionsSection.label3")} value={`${snapshot.opening_nav} ${snapshot.base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label4")} value={`${snapshot.cash} ${snapshot.base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label5")} value={`${snapshot.positions_mv} ${snapshot.base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label6")} value={`${snapshot.closing_nav} ${snapshot.base_currency}`} />
      </div>
    </Card>
  );
}

function PnLBreakdownCard({ pnl }: { pnl: ParsedPnLBreakdown }) {
  const { t } = useTranslation();
  if (pnl.kind === "unsupported_schema_version") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">{t("legacy.portfolioPositionsSection.t7", { string: String(pnl.received) })}</Alert>
      </Card>
    );
  }
  if (pnl.kind !== "ok") {
    return (
      <Card data-testid="pnl-breakdown-error">
        <Alert tone="danger">{t("legacy.portfolioPositionsSection.t8")}</Alert>
      </Card>
    );
  }
  const { realized, unrealized, fees, funding, total, base_currency } = pnl.value;
  return (
    <Card data-testid="pnl-breakdown-card">
      <CardTitle>{t("legacy.portfolioPositionsSection.t9")}</CardTitle>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <Stat label={t("legacy.portfolioPositionsSection.label10")} value={`${realized} ${base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label11")} value={`${unrealized} ${base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label12")} value={`${fees} ${base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label13")} value={`${funding} ${base_currency}`} />
        <Stat label={t("legacy.portfolioPositionsSection.label14")} value={`${total} ${base_currency}`} />
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
  const { t } = useTranslation();
  const freshness = deriveFreshness(asOf, now ?? new Date(), { staleAfterSec: STALE_AFTER_SEC });
  const isStale = freshness.kind === "ok" && freshness.isStale;

  return (
    <div className="space-y-4" data-testid="portfolio-positions-section">
      {isStale && (
        <div data-testid="positions-stale-banner">
          <Alert tone="warning">
            {t("legacy.portfolioPositionsSection.t15")}</Alert>
        </div>
      )}

      {nav !== undefined && <NavCard nav={nav} />}
      {pnl !== undefined && <PnLBreakdownCard pnl={pnl} />}

      <Card>
        <CardTitle>{t("legacy.portfolioPositionsSection.t16")}</CardTitle>
        {positions.length === 0 ? (
          <EmptyState>{t("legacy.portfolioPositionsSection.t17")}</EmptyState>
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
