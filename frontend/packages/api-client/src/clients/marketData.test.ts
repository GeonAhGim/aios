import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
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

const seriesKey = {
  venue: "BITGET",
  instrument_id: "11111111-1111-4111-8111-111111111111",
  timeframe: "1m",
};

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

const candleSeriesBody = {
  schema_version: "v1",
  key: seriesKey,
  candles: [candleRecord],
  gaps: [],
  adjustment: "RAW",
  as_of: "2026-09-03T00:05:00Z",
  series_hash: "abc123",
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
  instrumentId: "11111111-1111-4111-8111-111111111111",
  timeframe: "1m",
  start: "2026-09-01T00:00:00Z",
  end: "2026-09-03T00:00:00Z",
};

// LA-17(task-624, 7ad6d15) get_candles/replay_candles의 SSOT는 contracts/v1.py의
// CandleSeries·ReplaySeries다 — parseCandleSeries(candleSeries.ts, task-629)를
// 재사용해 결과를 판별 가능한 객체로 돌려주는지, 파싱 실패를 throw로 바꾸지
// 않는지를 5케이스로 고정한다.
describe("createMarketDataClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("경로는 apiPaths.ts 레지스트리에만 정의되어 있다(하드코딩 금지)", () => {
    expect(API_ROUTES["marketData.candles.get"].legacyPath).toBe("/v1/foundation/market-data/candles");
    expect(API_ROUTES["marketData.candles.replay"].legacyPath).toBe("/v1/foundation/market-data/candles/replay");
  });

  it("1) 정상 응답(ApiResponse 봉투 있음): parseCandleSeries가 봉투 안을 그대로 판별한다", async () => {
    const fetchMock = stubFetch({ data: candleSeriesBody, meta: { requestId: "r1" } });

    const result = await makeClient().getCandles(baseParams);

    expect(result.series.kind).toBe("ok");
    if (result.series.kind === "ok") {
      expect(result.series.value.series_hash).toBe("abc123");
      expect(result.series.value.candles).toHaveLength(1);
    }
    expect(result.quality).toBeNull();

    const url = requestUrl(fetchMock);
    expect(url).toBe(
      "https://api.example.test/v1/foundation/market-data/candles" +
        "?venue=BITGET&instrument_id=11111111-1111-4111-8111-111111111111&timeframe=1m" +
        "&start=2026-09-01T00%3A00%3A00Z&end=2026-09-03T00%3A00%3A00Z&adjustment=RAW",
    );
  });

  it("2) 정상 응답(봉투 없음): parseCandleSeries가 raw 그대로도 판별한다", async () => {
    stubFetch(candleSeriesBody);

    const result = await makeClient().getCandles(baseParams);

    expect(result.series.kind).toBe("ok");
    expect(result.quality).toBeNull();
  });

  it("3) schema_version 불일치: throw하지 않고 unsupported_schema_version을 반환한다", async () => {
    stubFetch({ ...candleSeriesBody, schema_version: "v2" });

    const result = await makeClient().getCandles(baseParams);

    expect(result.series).toEqual({ kind: "unsupported_schema_version", received: "v2" });
  });

  it("4) QualityVerdict≠ACCEPT: series는 그대로 ok이고, 부가 quality 필드는 parseQualityVerdict로 판별해 REJECT를 숨기지 않는다", async () => {
    stubFetch({ ...candleSeriesBody, quality: rejectQualityVerdictBody });

    const result = await makeClient().getCandles(baseParams);

    expect(result.series.kind).toBe("ok");
    expect(result.quality).toEqual({ kind: "ok", value: rejectQualityVerdictBody });
    if (result.quality?.kind === "ok") {
      expect(result.quality.value.verdict).toBe("REJECT");
    }
  });

  it("5) 미지 timeframe 거부: 요청 전에 reject하고 fetch를 호출하지 않는다", async () => {
    const fetchMock = stubFetch(candleSeriesBody);
    const client = makeClient();

    await expect(
      client.getCandles({ ...baseParams, timeframe: "2h" as CandleQueryParams["timeframe"] }),
    ).rejects.toThrow(/timeframe/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("replayCandles: replay 경로로 요청하고 asOf를 쿼리에 싣는다(필수)", async () => {
    const fetchMock = stubFetch(candleSeriesBody);

    await makeClient().replayCandles({ ...baseParams, asOf: "2026-09-03T00:00:00Z" });

    const url = requestUrl(fetchMock);
    expect(url).toContain("/v1/foundation/market-data/candles/replay");
    expect(url).toContain("as_of=2026-09-03T00%3A00%3A00Z");
  });

  it("replayCandles도 미지 timeframe을 요청 전에 거부한다", async () => {
    const fetchMock = stubFetch(candleSeriesBody);
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
});
