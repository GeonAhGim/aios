import type { MarketDataClient } from "@aios/api-client";
import { createMarketDataClient } from "@aios/api-client";
import type { InstrumentView, ParsedInstrumentView, SymbolStatus, Venue } from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Button, Card, EmptyState, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorBanner, InstrumentDetailPanel } from "../../components/InstrumentDetailPanel";
import { InstrumentLifecycleBadge } from "../../components/InstrumentLifecycleBadge";
import { useCursorPage } from "../../hooks/useCursorPage";

// spec §3.1 InstrumentView 목록·§4.2 심볼 생애주기. task-708(instrumentView.ts:
// parseInstrumentView/parseSymbolAlias, InstrumentLifecycleBadge)과 task-462
// (useCursorPage)를 처음으로 실제 라우트에 배선한다 — 새 파서·새 커서 로직은
// 만들지 않는다. 목록·별칭 조회 메서드는 marketData.ts(task-824)에 이 leaf가
// 새로 추가한 최소 메서드다(apiPaths.ts 레지스트리 규칙, LA-9 포트에는 아직
// 목록·별칭 조회가 없어 legacy-only 경로로 등록했다). 행 클릭 상세(별칭·생애주기
// 이력)는 InstrumentDetailPanel.tsx로 분리했다(300줄 규율).
//
// task-1088: 행의 "캔들 보기" 링크가 CandlesPage(/market/candles)로
// instrument_id를 쿼리스트링으로 인계한다(SeriesKey.instrument_id §3.1 SSOT).
// CandlesPage는 이 값을 그대로 받을 뿐 새 파서를 쓰지 않는다.
const VENUE_OPTIONS: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const STATUS_OPTIONS: readonly SymbolStatus[] = ["PENDING", "LISTED", "SUSPENDED", "DELISTED"];

interface InstrumentRowProps {
  parsed: ParsedInstrumentView;
  now: string;
  onSelect: () => void;
}

function InstrumentRow({ parsed, now, onSelect }: InstrumentRowProps) {
  if (parsed.kind !== "ok") {
    return (
      <li className="py-3">
        <InstrumentLifecycleBadge instrument={parsed} now={now} />
      </li>
    );
  }
  const { instrument_id: instrumentId, asset_class: assetClass } = parsed.value;
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-3">
      <button
        type="button"
        data-testid={`instrument-row-${instrumentId}`}
        onClick={onSelect}
        className="flex flex-1 flex-wrap items-center gap-2 text-left"
      >
        <InstrumentLifecycleBadge instrument={parsed} now={now} />
        <span className="text-xs text-fg-muted">{assetClass}</span>
      </button>
      <Link
        to={`/market/candles?instrument_id=${encodeURIComponent(instrumentId)}`}
        data-testid={`instrument-candles-link-${instrumentId}`}
        className="text-xs underline"
      >
        캔들 보기
      </Link>
    </li>
  );
}

export interface InstrumentsPageProps {
  marketDataClient?: Pick<MarketDataClient, "listInstruments" | "listInstrumentAliases">;
  now?: Date;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function InstrumentsPage({ marketDataClient, now }: InstrumentsPageProps) {
  const client = useMemo<Pick<MarketDataClient, "listInstruments" | "listInstrumentAliases">>(() => {
    if (marketDataClient) return marketDataClient;
    const getToken = () => useAuthStore.getState().token;
    return createMarketDataClient(baseUrl, getToken);
  }, [marketDataClient]);

  const [venue, setVenue] = useState<Venue | "">("");
  const [status, setStatus] = useState<SymbolStatus | "">("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [committed, setCommitted] = useState<{ cursor: string | undefined; nextCursor: string | null } | null>(null);
  const cursorPage = useCursorPage(committed ? { next_cursor: committed.nextCursor } : null);

  const query = useQuery({
    queryKey: ["instruments", venue, status, cursorPage.cursor],
    queryFn: () =>
      client.listInstruments({ venue: venue || undefined, status: status || undefined, cursor: cursorPage.cursor }),
  });

  if (query.data && (!committed || committed.cursor !== cursorPage.cursor)) {
    setCommitted({ cursor: cursorPage.cursor, nextCursor: query.data.nextCursor });
  }

  function resetPaging() {
    cursorPage.reset();
    setCommitted(null);
    setSelectedId(null);
  }

  const items = query.data?.items ?? [];
  const selectedMatch = items.find((parsed) => parsed.kind === "ok" && parsed.value.instrument_id === selectedId);
  const selectedInstrument: InstrumentView | null =
    selectedMatch && selectedMatch.kind === "ok" ? selectedMatch.value : null;

  const aliasQuery = useQuery({
    queryKey: ["instrumentAliases", selectedId],
    queryFn: () => client.listInstrumentAliases(selectedId as string),
    enabled: selectedId !== null,
  });

  const nowIso = (now ?? new Date()).toISOString();

  return (
    <AppShell>
      <div className="max-w-4xl space-y-6">
        <PageHeader title="심볼(Instrument)" />

        <Card>
          <div className="flex flex-wrap gap-3">
            <Select
              aria-label="venue 필터"
              value={venue}
              onChange={(e) => {
                setVenue(e.target.value as Venue | "");
                resetPaging();
              }}
            >
              <option value="">전체 venue</option>
              {VENUE_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </Select>
            <Select
              aria-label="status 필터"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as SymbolStatus | "");
                resetPaging();
              }}
            >
              <option value="">전체 status</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </div>
        </Card>

        {query.isError ? (
          <ErrorBanner error={query.error} onRetry={() => query.refetch()} />
        ) : query.isLoading ? (
          <LoadingState />
        ) : items.length === 0 ? (
          <EmptyState>표시할 심볼이 없습니다.</EmptyState>
        ) : (
          <Card>
            <ul className="divide-y divide-border">
              {items.map((parsed, index) => (
                <InstrumentRow
                  key={parsed.kind === "ok" ? parsed.value.instrument_id : `invalid-${index}`}
                  parsed={parsed}
                  now={nowIso}
                  onSelect={() => {
                    if (parsed.kind !== "ok") return;
                    setSelectedId(parsed.value.instrument_id === selectedId ? null : parsed.value.instrument_id);
                  }}
                />
              ))}
            </ul>
          </Card>
        )}

        {!query.isError && (query.data || cursorPage.hasPrev) && (
          <div className="flex items-center justify-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!cursorPage.hasPrev}
              onClick={() => {
                cursorPage.prev();
                setSelectedId(null);
              }}
            >
              이전
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!cursorPage.hasNext}
              onClick={() => {
                cursorPage.next();
                setSelectedId(null);
              }}
            >
              다음
            </Button>
          </div>
        )}

        {selectedInstrument && <InstrumentDetailPanel instrument={selectedInstrument} aliasQuery={aliasQuery} />}
      </div>
    </AppShell>
  );
}
