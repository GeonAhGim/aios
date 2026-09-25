import { vi } from "vitest";
import { createMarketDataClient, type CandleQueryParams, type CoverageQueryParams } from "./marketData";

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function makeClient() {
  return createMarketDataClient("https://api.example.test", () => null);
}

export function requestUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
  return url;
}

// task-1525: fixture는 LA-24(task-1376) 실라우터의 응답 JSON 형태를 그대로 쓴다 —
// src/api/contracts/envelope.py ApiResponse{data, meta{trace_id, as_of, page}} 봉투 안에
// src/api/schemas/market_data.py CandleSeriesView(= contracts/v1 CandleSeries + instrument_id
// /symbol/canonical_symbol/entitlement) · ReplaySeriesView · InstrumentListView{items,
// next_cursor} · list[SymbolAliasRef]가 들어간다. parseCandleSeries/parseInstrumentView는
// 계약 필드만 읽으므로 부가 필드(entitlement 등)에 영향받지 않아야 한다(무수정 확인).
export const INSTRUMENT_ID = "11111111-1111-4111-8111-111111111111";
export const TRACE_ID = "22222222-2222-4222-8222-222222222222";

export const seriesKey = { venue: "BITGET", instrument_id: INSTRUMENT_ID, timeframe: "1m" };

export const candleRecord = {
  key: seriesKey,
  open_time: "2026-09-03T00:00:00Z",
  close_time: "2026-09-03T00:01:00Z",
  open: "100.0",
  high: "101.0",
  low: "99.0",
  close: "100.5",
  volume: "10.0",
  quote_volume: null,
};

export const entitlement = { mode: "delayed", delayed_seconds: 0 };

// market_data.py:129-134 CandleSeriesView(계약 필드 + 식별자·이용권 부가 필드).
export const candleSeriesView = {
  schema_version: "v1",
  key: seriesKey,
  candles: [candleRecord],
  gaps: [],
  adjustment: "RAW",
  as_of: "2026-09-03T00:05:00Z",
  series_hash: "abc123",
  instrument_id: INSTRUMENT_ID,
  symbol: "BTCUSDT",
  canonical_symbol: "BTC/USDT",
  entitlement,
};

// market_data.py:175-180 ReplaySeriesView(expected_count/missing_count 추가, 페이지 없음).
export const replaySeriesView = { ...candleSeriesView, expected_count: 1, missing_count: 0 };

export function envelope(data: unknown, page: Record<string, unknown> | null = null) {
  return { data, meta: { trace_id: TRACE_ID, as_of: "2026-09-03T00:05:01Z", page } };
}

export const candlePage = { total: null, page: null, size: 500, next_cursor: null };

// contracts/v1.py InstrumentRef(schema_version 포함) 그대로 — InstrumentListView.items 항목.
export const instrumentRef = {
  instrument_id: INSTRUMENT_ID,
  venue: "BITGET",
  canonical_symbol: "BTC/USDT",
  venue_symbol: "BTCUSDT",
  asset_class: "CRYPTO",
  base: "BTC",
  quote: "USDT",
  tick_size: "0.1",
  lot_size: "0.001",
  status: "LISTED",
  listed_at: "2024-01-01T00:00:00Z",
  delisted_at: null,
  schema_version: "v1",
};

// ports/reference_repository.py:61 SymbolAliasRef 그대로 — aliases 응답 data는 배열.
export const symbolAliasRef = {
  alias_id: "33333333-3333-4333-8333-333333333333",
  instrument_id: INSTRUMENT_ID,
  venue: "BITGET",
  alias_symbol: "XBTUSDT",
  valid_from: "2024-01-01T00:00:00Z",
  valid_to: null,
};

// 409 DATA_COVERAGE_MISSING 에러 봉투(envelope.py ApiError, error_codes.py:63·:95).
export const coverageMissingError = {
  error_code: "DATA_COVERAGE_MISSING",
  message: "요청 구간 [2026-09-01T00:00:00+00:00, 2026-09-03T00:00:00+00:00)에 저장된 캔들이 없습니다.",
  details: {},
  trace_id: TRACE_ID,
  retry_after_seconds: null,
};

export const rejectQualityVerdictBody = {
  schema_version: "v1",
  verdict: "REJECT",
  accepted: 0,
  quarantined: 0,
  rejected: 1,
  issues: [
    { type: "OHLC_INCONSISTENT", severity: "REJECT", open_time: "2026-09-03T00:00:00Z", detail: { reason: "high<low" } },
  ],
};

export const baseParams: CandleQueryParams = {
  venue: "BITGET",
  instrumentId: INSTRUMENT_ID,
  timeframe: "1m",
  start: "2026-09-01T00:00:00Z",
  end: "2026-09-03T00:00:00Z",
};

// task-2196(DC-18b): DC-6 contracts/v2/coverage.py CoverageSpan 목록.
export const coverageParams: CoverageQueryParams = { instrumentId: INSTRUMENT_ID, venue: "BITGET", timeframe: "1h" };

export const coverageSpanRaw = {
  instrument_id: INSTRUMENT_ID,
  venue: "BITGET",
  asset_class: "CRYPTO",
  timeframe: "1h",
  quality_grade: "VALIDATED",
  start_at: "2026-09-03T00:00:00Z",
  end_at: "2026-09-03T04:00:00Z",
};
