import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES, resolveEnvelope } from "../apiPaths";
import { ApiError } from "../httpErrors";
import { createMarketDataClient, type CandleQueryParams } from "./marketData";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient() {
  return createMarketDataClient("https://api.example.test", () => null);
}

function requestUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
  return url;
}

// task-1525: fixture는 LA-24(task-1376) 실라우터의 응답 JSON 형태를 그대로 쓴다 —
// src/api/contracts/envelope.py ApiResponse{data, meta{trace_id, as_of, page}} 봉투 안에
// src/api/schemas/market_data.py CandleSeriesView(= contracts/v1 CandleSeries + instrument_id
// /symbol/canonical_symbol/entitlement) · ReplaySeriesView · InstrumentListView{items,
// next_cursor} · list[SymbolAliasRef]가 들어간다. parseCandleSeries/parseInstrumentView는
// 계약 필드만 읽으므로 부가 필드(entitlement 등)에 영향받지 않아야 한다(무수정 확인).
const INSTRUMENT_ID = "11111111-1111-4111-8111-111111111111";
const TRACE_ID = "22222222-2222-4222-8222-222222222222";

const seriesKey = { venue: "BITGET", instrument_id: INSTRUMENT_ID, timeframe: "1m" };

const candleRecord = {
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

const entitlement = { mode: "delayed", delayed_seconds: 0 };

// market_data.py:129-134 CandleSeriesView(계약 필드 + 식별자·이용권 부가 필드).
const candleSeriesView = {
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
const replaySeriesView = { ...candleSeriesView, expected_count: 1, missing_count: 0 };

function envelope(data: unknown, page: Record<string, unknown> | null = null) {
  return { data, meta: { trace_id: TRACE_ID, as_of: "2026-09-03T00:05:01Z", page } };
}

const candlePage = { total: null, page: null, size: 500, next_cursor: null };

// contracts/v1.py InstrumentRef(schema_version 포함) 그대로 — InstrumentListView.items 항목.
const instrumentRef = {
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
const symbolAliasRef = {
  alias_id: "33333333-3333-4333-8333-333333333333",
  instrument_id: INSTRUMENT_ID,
  venue: "BITGET",
  alias_symbol: "XBTUSDT",
  valid_from: "2024-01-01T00:00:00Z",
  valid_to: null,
};

// 409 DATA_COVERAGE_MISSING 에러 봉투(envelope.py ApiError, error_codes.py:63·:95).
const coverageMissingError = {
  error_code: "DATA_COVERAGE_MISSING",
  message: "요청 구간 [2026-09-01T00:00:00+00:00, 2026-09-03T00:00:00+00:00)에 저장된 캔들이 없습니다.",
  details: {},
  trace_id: TRACE_ID,
  retry_after_seconds: null,
};

const rejectQualityVerdictBody = {
  schema_version: "v1",
  verdict: "REJECT",
  accepted: 0,
  quarantined: 0,
  rejected: 1,
  issues: [
    { type: "OHLC_INCONSISTENT", severity: "REJECT", open_time: "2026-09-03T00:00:00Z", detail: { reason: "high<low" } },
  ],
};

const baseParams: CandleQueryParams = {
  venue: "BITGET",
  instrumentId: INSTRUMENT_ID,
  timeframe: "1m",
  start: "2026-09-01T00:00:00Z",
  end: "2026-09-03T00:00:00Z",
};

describe("createMarketDataClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("경로·봉투 여부는 apiPaths.ts 레지스트리에만 정의되어 있다(4경로 전부 envelope=true, task-1525)", () => {
    expect(API_ROUTES["marketData.candles.get"].legacyPath).toBe("/v1/foundation/market-data/candles");
    expect(API_ROUTES["marketData.candles.replay"].legacyPath).toBe("/v1/foundation/market-data/candles/replay");
    for (const route of [
      "marketData.candles.get",
      "marketData.candles.replay",
      "marketData.instruments.list",
      "marketData.instruments.aliases",
    ] as const) {
      expect(resolveEnvelope(route)).toBe(true);
    }
  });

  it("1) 정상 응답(실응답 CandleSeriesView 봉투): 계약 필드로 ok 판별하고 부가 필드(entitlement 등)에 깨지지 않는다", async () => {
    const fetchMock = stubFetch(envelope(candleSeriesView, candlePage));

    const result = await makeClient().getCandles(baseParams);

    expect(result.series.kind).toBe("ok");
    if (result.series.kind === "ok") {
      expect(result.series.value.series_hash).toBe("abc123");
      expect(result.series.value.candles).toHaveLength(1);
      expect(result.series.value.candles[0].quote_volume).toBeNull();
    }
    expect(result.quality).toBeNull();

    const url = requestUrl(fetchMock);
    expect(url).toBe(
      "https://api.example.test/v1/foundation/market-data/candles" +
        `?venue=BITGET&instrument_id=${INSTRUMENT_ID}&timeframe=1m` +
        "&start=2026-09-01T00%3A00%3A00Z&end=2026-09-03T00%3A00%3A00Z&adjustment=RAW",
    );
  });

  it("2) negative: 봉투 없는 응답은 LA-24 계약 위반이라 조용히 파싱하지 않고 throw한다(envelope=true)", async () => {
    stubFetch(candleSeriesView);

    await expect(makeClient().getCandles(baseParams)).rejects.toThrow(/봉투 형식/);
  });

  it("3) schema_version 불일치: throw하지 않고 unsupported_schema_version을 반환한다", async () => {
    stubFetch(envelope({ ...candleSeriesView, schema_version: "v2" }, candlePage));

    const result = await makeClient().getCandles(baseParams);

    expect(result.series).toEqual({ kind: "unsupported_schema_version", received: "v2" });
  });

  it("4) QualityVerdict≠ACCEPT: series는 그대로 ok이고, 부가 quality 필드는 parseQualityVerdict로 판별해 REJECT를 숨기지 않는다", async () => {
    stubFetch(envelope({ ...candleSeriesView, quality: rejectQualityVerdictBody }, candlePage));

    const result = await makeClient().getCandles(baseParams);

    expect(result.series.kind).toBe("ok");
    expect(result.quality).toEqual({ kind: "ok", value: rejectQualityVerdictBody });
    if (result.quality?.kind === "ok") {
      expect(result.quality.value.verdict).toBe("REJECT");
    }
  });

  it("5) 미지 timeframe 거부: 요청 전에 reject하고 fetch를 호출하지 않는다", async () => {
    const fetchMock = stubFetch(envelope(candleSeriesView, candlePage));
    const client = makeClient();

    await expect(
      client.getCandles({ ...baseParams, timeframe: "2h" as CandleQueryParams["timeframe"] }),
    ).rejects.toThrow(/timeframe/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("6) negative: 409 DATA_COVERAGE_MISSING 에러 봉투는 ApiError(statusCode·errorCode)로 그대로 던진다(빈 시리즈로 뭉개지 않음)", async () => {
    stubFetch(coverageMissingError, 409);

    const err = await makeClient().getCandles(baseParams).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.statusCode).toBe(409);
      expect(err.errorCode).toBe("DATA_COVERAGE_MISSING");
      expect(err.traceId).toBe(TRACE_ID);
    }
  });

  it("replayCandles: 실응답 ReplaySeriesView 봉투를 ok로 판별하고 replay 경로에 asOf를 쿼리로 싣는다(필수)", async () => {
    const fetchMock = stubFetch(envelope(replaySeriesView));

    const result = await makeClient().replayCandles({ ...baseParams, asOf: "2026-09-03T00:00:00Z" });

    expect(result.series.kind).toBe("ok");
    const url = requestUrl(fetchMock);
    expect(url).toContain("/v1/foundation/market-data/candles/replay");
    expect(url).toContain("as_of=2026-09-03T00%3A00%3A00Z");
  });

  it("replayCandles도 미지 timeframe을 요청 전에 거부한다", async () => {
    const fetchMock = stubFetch(envelope(replaySeriesView));
    const client = makeClient();

    await expect(
      client.replayCandles({
        ...baseParams,
        timeframe: "2h" as CandleQueryParams["timeframe"],
        asOf: "2026-09-03T00:00:00Z",
      }),
    ).rejects.toThrow(/timeframe/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("listInstruments: 실응답 InstrumentListView 봉투의 items를 parseInstrumentView로 항목별 판별하고 data.next_cursor를 돌려준다", async () => {
    const fetchMock = stubFetch(
      envelope(
        { items: [instrumentRef], next_cursor: INSTRUMENT_ID },
        { total: null, page: null, size: 50, next_cursor: INSTRUMENT_ID },
      ),
    );

    const result = await makeClient().listInstruments({ venue: "BITGET", status: "LISTED", cursor: INSTRUMENT_ID });

    expect(result.items).toHaveLength(1);
    expect(result.items[0].kind).toBe("ok");
    if (result.items[0].kind === "ok") {
      expect(result.items[0].value.venue_symbol).toBe("BTCUSDT");
    }
    expect(result.nextCursor).toBe(INSTRUMENT_ID);
    expect(requestUrl(fetchMock)).toBe(
      `https://api.example.test/v1/foundation/market-data/instruments?venue=BITGET&status=LISTED&cursor=${INSTRUMENT_ID}`,
    );
  });

  it("listInstrumentAliases: 실응답 list[SymbolAliasRef] 봉투를 parseSymbolAlias로 항목별 판별하고 경로 세그먼트에 UUID를 쓴다", async () => {
    const fetchMock = stubFetch(envelope([symbolAliasRef]));

    const result = await makeClient().listInstrumentAliases(INSTRUMENT_ID);

    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe("ok");
    if (result[0].kind === "ok") {
      expect(result[0].value.alias_symbol).toBe("XBTUSDT");
    }
    expect(requestUrl(fetchMock)).toBe(
      `https://api.example.test/v1/foundation/market-data/instruments/${INSTRUMENT_ID}/aliases`,
    );
  });
});
