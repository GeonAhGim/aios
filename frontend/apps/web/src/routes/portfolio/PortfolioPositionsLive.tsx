import type { NavSeriesResult } from "@aios/api-client";
import type { NAVSnapshot, ParsedNavSnapshot, ParsedPositionSnapshot } from "@aios/shared-types";
import { Field, LoadingState, Select } from "@aios/ui-web";
import { useMemo, useState } from "react";
import {
  useNavSeries,
  usePositionList,
  usePositionsClient,
  type PositionsClientLike,
} from "../../hooks/usePositions";
import { PortfolioPositionsSection } from "./PortfolioPositionsSection";
import { PositionJournalPanel } from "./PositionJournalPanel";
import { PositionsQueryError } from "./PositionsQueryError";

// task-1524(LB-19): task-709가 "서버 라우트 대기"로 positions=[]로 두었던 자리를 실데이터로
// 연결한다. GET /v1/positions(테넌트 전체)로 스냅샷을, 그 스냅샷들의 account_id로
// GET /v1/positions/nav(최근 NAV_WINDOW_DAYS일)를 조회해 가장 최근 NAV 한 건을 NavCard에
// 준다. as_of는 봉투 meta.as_of만 쓴다(task-936 decision — Date.now() 대입 금지).
// PnL 분해(PnLBreakdown)는 LB-19에 라우트가 없어 연결하지 않는다(pnl prop 미전달).
//
// 계좌가 여럿이면 NAV 대상 계좌를 고르는 Select를 보여준다 — 여러 계좌 NAV를 합산하지
// 않는다(합산은 서버 SSOT의 몫, FD-3.3 "never assume zero").
const NAV_WINDOW_DAYS = 7;
const DAY_MS = 86_400_000;

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

type OkNav = Extract<ParsedNavSnapshot, { kind: "ok" }>;

// 정상 항목 중 nav_date가 가장 늦은 것. 정상 항목이 하나도 없으면(전부 파싱 실패) 첫
// 항목을 그대로 넘겨 NavCard가 실패 사유를 그리게 한다 — 조용히 숨기지 않는다.
function pickLatestNav(series: NavSeriesResult | undefined): ParsedNavSnapshot | undefined {
  if (!series || series.items.length === 0) return undefined;
  const ok = series.items.filter((item): item is OkNav => item.kind === "ok");
  if (ok.length === 0) return series.items[0];
  return ok.reduce((latest, item) => (item.value.nav_date > latest.value.nav_date ? item : latest));
}

function distinctAccountIds(items: ParsedPositionSnapshot[]): string[] {
  const ids = new Set<string>();
  for (const item of items) {
    if (item.kind === "ok") ids.add(item.value.account_id);
  }
  return [...ids].sort();
}

interface NavMissingNoticeProps {
  series: NavSeriesResult;
  latest: NAVSnapshot | undefined;
}

function NavMissingNotice({ series, latest }: NavMissingNoticeProps) {
  if (series.missingDates.length === 0) return null;
  return (
    <p className="text-xs text-fg-muted" data-testid="nav-missing-notice">
      {latest ? `NAV 미산출 ${series.missingDates.length}일 (최근 산출일 ${latest.nav_date})` : `최근 ${NAV_WINDOW_DAYS}일 NAV가 산출되지 않았습니다.`}
    </p>
  );
}

export interface PortfolioPositionsLiveProps {
  client?: PositionsClientLike;
  now?: Date;
}

export function PortfolioPositionsLive({ client: injected, now }: PortfolioPositionsLiveProps) {
  const client = usePositionsClient(injected);
  // 첫 렌더 시각으로 고정(CandlesPage anchor 관용) — start/end가 매 렌더 흔들려 queryKey가
  // 바뀌는 일이 없게 한다.
  const [anchor] = useState(() => now ?? new Date());
  const positionsQuery = usePositionList(client);
  const items = positionsQuery.data?.items;
  const accountIds = useMemo(() => distinctAccountIds(items ?? []), [items]);
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null);
  const accountId =
    selectedAccount !== null && accountIds.includes(selectedAccount) ? selectedAccount : (accountIds[0] ?? null);

  const navParams = accountId
    ? {
        accountId,
        startDate: isoDate(new Date(anchor.getTime() - (NAV_WINDOW_DAYS - 1) * DAY_MS)),
        endDate: isoDate(anchor),
      }
    : null;
  const navQuery = useNavSeries(client, navParams);

  if (positionsQuery.isError) {
    return (
      <div data-testid="portfolio-positions-error">
        <PositionsQueryError
          error={positionsQuery.error}
          notFoundTitle="포지션을 찾을 수 없습니다."
          onRetry={() => positionsQuery.refetch()}
        />
      </div>
    );
  }
  if (positionsQuery.isPending) {
    return <LoadingState />;
  }

  const latestNav = pickLatestNav(navQuery.data);

  return (
    <div className="space-y-3">
      {accountIds.length > 1 && (
        <Field label="NAV 계좌">
          <Select
            value={accountId ?? ""}
            onChange={(e) => setSelectedAccount(e.target.value)}
            data-testid="nav-account-select"
          >
            {accountIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {navQuery.isError && (
        <div data-testid="nav-series-error">
          <PositionsQueryError
            error={navQuery.error}
            notFoundTitle="NAV를 찾을 수 없습니다."
            onRetry={() => navQuery.refetch()}
          />
        </div>
      )}
      {navQuery.data && (
        <NavMissingNotice series={navQuery.data} latest={latestNav?.kind === "ok" ? latestNav.value : undefined} />
      )}

      <PortfolioPositionsSection
        positions={positionsQuery.data.items}
        nav={latestNav}
        asOf={positionsQuery.data.asOf}
        now={anchor}
        renderPositionExtra={(snapshot) => (
          <PositionJournalPanel client={client} positionKey={snapshot.position_key} />
        )}
      />
    </div>
  );
}
