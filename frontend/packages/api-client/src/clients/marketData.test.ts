import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES, resolveEnvelope } from "../apiPaths";
import { ApiError } from "../httpErrors";
import type { CandleQueryParams } from "./marketData";
import {
  baseParams,
  candlePage,
  candleSeriesView,
  coverageMissingError,
  envelope,
  INSTRUMENT_ID,
  instrumentRef,
  makeClient,
  rejectQualityVerdictBody,
  replaySeriesView,
  requestUrl,
  stubFetch,
  symbolAliasRef,
  TRACE_ID,
} from "./marketData.fixtures";

describe("createMarketDataClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
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

  // task-2196(DC-18b): coverage 경로는 위 목록에서 의도적으로 분리했다 — task-2195
  // decision 당시 openapi 스냅샷이 재생성되지 않아 apiPaths.openapi.test.ts의
  // STALE_SNAPSHOT_WHITELIST에 등재된 예외 경로이므로, 그 사정을 별도 테스트로
  // 남겨 향후 스냅샷이 갱신될 때 이 등재도 같이 지워야 함을 드러낸다.
  it("marketData.coverage.get 경로도 envelope=true로 등록되어 있다", () => {
    expect(API_ROUTES["marketData.coverage.get"].legacyPath).toBe("/v1/foundation/market-data/coverage");
    expect(resolveEnvelope("marketData.coverage.get")).toBe(true);
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
