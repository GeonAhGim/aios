import type { CandleQueryParams, CandleQueryResult } from "@aios/api-client";
import { ApiError, createMarketDataClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { routeApiError, type Timeframe, type Venue } from "@aios/shared-types";
import { CandlestickChart, type CandlestickPoint, EmptyState, Field, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CandleQualityBadge } from "../../components/CandleQualityBadge";
import { DataFreshness } from "../../components/DataFreshness";
import { ErrorMessage } from "../../components/ErrorMessage";
import { AppShell } from "../../components/layout/AppShell";
import { useTranslation } from "react-i18next";

// spec §3.1 market_data 계약. task-719(createMarketDataClient·getCandles)와
// task-629(parseCandleSeries·CandleQualityBadge)만 재사용해 배선한다 — 새
// 파서·클라이언트 로직은 만들지 않는다. 에러는 routeApiError+ErrorMessage
// 경로로만 렌더한다(err.message 직접 노출 금지, getApiErrorMessage가 매핑).
//
// task-1088: SeriesKey.instrument_id(§3.1)가 SSOT다 — 심볼 자유입력 필드를
// 없애고 라우트 쿼리스트링(?instrument_id=)으로만 받는다. InstrumentsPage
// 행의 "캔들 보기" 링크가 이 쿼리스트링을 만든다(task-824 InstrumentView.
// instrument_id 재사용, 파서 재구현 금지). 직접 진입해 instrument_id가
// 없으면 자유입력으로 폴백하지 않고 InstrumentsPage로 가는 안내만 보여준다
// (task-837이 이관한 결함의 정식 해소). 표시하는 instrument_id 문자열도
// InstrumentView.instrument_id 값 그대로다 — 클라이언트에서 조합하지 않는다.

const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const TIMEFRAME_MS: Record<Timeframe, number> = {
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "30m": 30 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};
// 화면 선택 UI는 거래소·타임프레임만 노출한다(DoD) — 조회 범위(start/end)는
// timeframe에서 캔들 200개치를 파생시켜 정한다, 별도 상태로 두지 않는다.
const VISIBLE_CANDLE_COUNT = 200;

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const marketDataClient = createMarketDataClient(baseUrl, () => useAuthStore.getState().token);

export type FetchCandles = (params: CandleQueryParams) => Promise<CandleQueryResult>;

interface CandlesPageProps {
  fetchCandles?: FetchCandles;
  now?: Date;
}

function toChartPoints(series: CandleQueryResult["series"]): CandlestickPoint[] {
  if (series.kind !== "ok") return [];
  return series.value.candles.map((c) => ({
    time: Math.floor(Date.parse(c.open_time) / 1000),
    open: Number(c.open),
    high: Number(c.high),
    low: Number(c.low),
    close: Number(c.close),
  }));
}

function NoInstrumentSelected() {
  const { t } = useTranslation();
  return (
    <AppShell>
      <div className="max-w-3xl space-y-4">
        <PageHeader title={t("legacy.candlesPage.title1")} />
        <EmptyState>
          {t("legacy.candlesPage.t2", { val: " " })}
          <Link to="/market/instruments" className="underline">
            {t("legacy.candlesPage.t3")}</Link>
        </EmptyState>
      </div>
    </AppShell>
  );
}

export function CandlesPage({ fetchCandles = marketDataClient.getCandles, now }: CandlesPageProps) {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const instrumentId = searchParams.get("instrument_id");
  const [venue, setVenue] = useState<Venue>(VENUES[0]);
  const [timeframe, setTimeframe] = useState<Timeframe>("1h");
  // now는 렌더마다 새 Date를 만들지 않도록 최초 1회만 고정한다(테스트 주입 지원,
  // DataFreshness의 now prop과 동일 관용) — 그래야 start/end가 매 렌더 바뀌어
  // queryKey가 흔들리는 일이 없다.
  const [anchor] = useState(() => now ?? new Date());

  const end = anchor.toISOString();
  const start = new Date(anchor.getTime() - VISIBLE_CANDLE_COUNT * TIMEFRAME_MS[timeframe]).toISOString();

  const query = useQuery({
    queryKey: ["candles", venue, instrumentId, timeframe, start, end],
    queryFn: () => fetchCandles({ venue, instrumentId: instrumentId as string, timeframe, start, end }),
    enabled: instrumentId !== null && instrumentId.trim().length > 0,
  });

  if (!instrumentId) {
    return <NoInstrumentSelected />;
  }

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";
  const series = query.data?.series;
  const asOf = series?.kind === "ok" ? series.value.as_of : undefined;
  const points = series ? toChartPoints(series) : [];

  return (
    <AppShell>
      <div className="max-w-3xl space-y-4">
        <PageHeader title={t("legacy.candlesPage.title4")} action={asOf && <DataFreshness asOf={asOf} now={anchor} />} />

        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("legacy.candlesPage.label5")}>
            <Select value={venue} onChange={(e) => setVenue(e.target.value as Venue)}>
              {VENUES.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("legacy.candlesPage.label6")}>
            <p className="px-3 py-2 text-sm text-fg" data-testid="candles-instrument-id">
              {instrumentId}
            </p>
          </Field>
          <Field label={t("legacy.candlesPage.label7")}>
            <Select value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
              {TIMEFRAMES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {query.isError ? (
          <ErrorMessage
            errorCode={query.error instanceof ApiError ? query.error.errorCode : undefined}
            message={query.error instanceof Error ? query.error.message : undefined}
            traceId={query.error instanceof ApiError ? query.error.traceId : undefined}
            retryAfterSec={routed?.kind === "backoff_retry" ? routed.afterSec : undefined}
            onRetry={canRetry ? () => query.refetch() : undefined}
          />
        ) : (
          <>
            {series && <CandleQualityBadge series={series} verdict={query.data?.quality ?? undefined} />}
            {query.isLoading ? (
              <LoadingState />
            ) : points.length === 0 ? (
              <EmptyState>{t("legacy.candlesPage.t8")}</EmptyState>
            ) : (
              <CandlestickChart data={points} />
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
