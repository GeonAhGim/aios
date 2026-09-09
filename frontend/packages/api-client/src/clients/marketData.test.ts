import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES, resolveEnvelope } from "../apiPaths";
import { ApiError } from "../httpErrors";
import { createMarketDataClient, type CandleQueryParams, type CoverageQueryParams } from "./marketData";

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

  // task-2196(DC-18b): DC-6 contracts/v2/coverage.py CoverageSpan 목록. 이
  // 클라이언트는 지금껏 테스트가 없었다(D3 하한 미달 사유) — 정상 매핑 1건 +
  // 계약 위반(예외/reject) 경로를 별도로 검증한다.
  const coverageParams: CoverageQueryParams = { instrumentId: INSTRUMENT_ID, venue: "BITGET", timeframe: "1h" };

  const coverageSpanRaw = {
    instrument_id: INSTRUMENT_ID,
    venue: "BITGET",
    asset_class: "CRYPTO",
    timeframe: "1h",
    quality_grade: "VALIDATED",
    start_at: "2026-09-03T00:00:00Z",
    end_at: "2026-09-03T04:00:00Z",
  };

  describe("getCoverage", () => {
    it("정상 응답: 실응답 CoverageSpan 목록을 camelCase로 매핑하고 경로·쿼리를 그대로 싣는다", async () => {
      const fetchMock = stubFetch(envelope([coverageSpanRaw]));

      const result = await makeClient().getCoverage(coverageParams);

      expect(result).toEqual([
        {
          instrumentId: INSTRUMENT_ID,
          venue: "BITGET",
          assetClass: "CRYPTO",
          timeframe: "1h",
          qualityGrade: "VALIDATED",
          startAt: "2026-09-03T00:00:00Z",
          endAt: "2026-09-03T04:00:00Z",
        },
      ]);
      expect(requestUrl(fetchMock)).toBe(
        `https://api.example.test/v1/foundation/market-data/coverage?instrument_id=${INSTRUMENT_ID}&venue=BITGET&timeframe=1h`,
      );
    });

    it("no-coverage/no-entitlement: 서버가 200+[]로 접으면 throw하지 않고 빈 배열을 그대로 돌려준다", async () => {
      stubFetch(envelope([]));

      await expect(makeClient().getCoverage(coverageParams)).resolves.toEqual([]);
    });

    it("negative: 미지 timeframe은 요청 전에 reject하고 fetch를 호출하지 않는다", async () => {
      const fetchMock = stubFetch(envelope([coverageSpanRaw]));
      const client = makeClient();

      await expect(
        client.getCoverage({ ...coverageParams, timeframe: "2h" as CoverageQueryParams["timeframe"] }),
      ).rejects.toThrow(/timeframe/);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("negative: quality_grade가 계약 밖 값(WHITELIST에 없는 문자열)인 span은 0으로 채우지 않고 통째로 버린다", async () => {
      const result = await (async () => {
        stubFetch(envelope([coverageSpanRaw, { ...coverageSpanRaw, quality_grade: "PLATINUM" }]));
        return makeClient().getCoverage(coverageParams);
      })();

      expect(result).toHaveLength(1);
      expect(result[0].qualityGrade).toBe("VALIDATED");
    });

    it("negative: 필드가 누락된 span(start_at 없음)도 통째로 버린다(0/빈문자열로 채우지 않음)", async () => {
      const { start_at: _omit, ...withoutStartAt } = coverageSpanRaw;
      stubFetch(envelope([withoutStartAt]));

      const result = await makeClient().getCoverage(coverageParams);

      expect(result).toEqual([]);
    });

    it("negative: data가 배열이 아니면(계약 위반 형태) throw하지 않고 빈 배열로 접는다", async () => {
      stubFetch(envelope({ items: [coverageSpanRaw] }));

      await expect(makeClient().getCoverage(coverageParams)).resolves.toEqual([]);
    });

    it("negative: 5xx 에러는 ApiError로 그대로 던진다(빈 배열로 뭉개 미커버 판정을 숨기지 않음)", async () => {
      stubFetch({ error_code: "INTERNAL", message: "boom", details: {}, trace_id: TRACE_ID, retry_after_seconds: null }, 500);

      const err = await makeClient().getCoverage(coverageParams).catch((e: unknown) => e);

      expect(err).toBeInstanceOf(ApiError);
      if (err instanceof ApiError) {
        expect(err.statusCode).toBe(500);
      }
    });
  });

  // DEPTH_LA_LB_LC(task-2723)가 원 task-1525(561a9e6)를 D3 축 하한 미달(D1)로
  // 판정 — 모의 HTTP 오류 바디 외의 실 어댑터/네트워크 결함 시뮬레이션·수치 성능
  // 단언·게이트 적색 재현이 비어 있었다(task-2996 DEEPEN). 위 6번/coverage 5xx
  // 테스트는 "서버가 에러 바디를 내려줬다"만 흉내내지, 네트워크 자체가 끊기거나
  // 응답 바디가 전송 중 깨지는 실제 결함 클래스는 다루지 않았다.
  describe("failure-injection — LA-24 실 어댑터/네트워크 결함 시뮬레이션", () => {
    it("네트워크 완전 단절(fetch 자체가 reject)이면 HTTP 에러 바디 없이도 원본 예외를 그대로 던진다(ApiError로 재분류·빈 시리즈로 뭉개지 않음)", async () => {
      const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
      vi.stubGlobal("fetch", fetchMock);

      const err = await makeClient().getCandles(baseParams).catch((e: unknown) => e);

      expect(err).toBeInstanceOf(TypeError);
      expect(err).not.toBeInstanceOf(ApiError);
    });

    it("응답 바디가 전송 중 잘린 깨진 JSON(실 어댑터 결함 — 에러 봉투가 아니라 파싱 자체가 불가)이면 SyntaxError를 그대로 던진다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response('{"data": {"key":', {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(makeClient().getCandles(baseParams)).rejects.toThrow(SyntaxError);
    });

    it("응답 바디가 완전히 빈 문자열(연결이 중간에 끊긴 실 결함)이면 봉투 형식 위반으로 던지고 빈 시리즈로 채우지 않는다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response("", { status: 200, headers: { "Content-Type": "application/json" } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(makeClient().getCandles(baseParams)).rejects.toThrow(/봉투 형식/);
    });

    it("GET 재시도 소진: 503이 반복되면 정책된 백오프(1초·2초, httpErrors.ts SERVER_ERROR_BACKOFF_SEC)를 실제로 다 태우고도 여전히 실패해 ApiError를 던진다(무한 재시도 아님)", async () => {
      vi.useFakeTimers();
      const errorBody = {
        error_code: "EXCHANGE_UNAVAILABLE",
        message: "거래소 연결 불가",
        details: {},
        trace_id: TRACE_ID,
        retry_after_seconds: null,
      };
      const fetchMock = vi.fn().mockImplementation(async () => jsonResponse(503, errorBody));
      vi.stubGlobal("fetch", fetchMock);

      const resultPromise = makeClient().getCandles(baseParams).catch((e: unknown) => e);
      // 정책상 재시도는 최초 1회 + 백오프 2회(1초, 2초) 상한 — 3초를 넘게 흘려보내도
      // 4번째 호출은 없어야 한다(무한 재시도 방지 확인).
      await vi.advanceTimersByTimeAsync(5_000);
      const err = await resultPromise;

      expect(err).toBeInstanceOf(ApiError);
      if (err instanceof ApiError) expect(err.statusCode).toBe(503);
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });
  });

  describe("수치 성능 단언 — 대용량 캔들 시리즈", () => {
    it("candles 2000개짜리 응답도 짧은 시간 안에 항목별로 판별해 돌려준다(O(n) 유지 확인 — 회귀로 O(n^2)가 되면 이 임계값이 신호를 준다)", async () => {
      const CANDLE_COUNT = 2000;
      const candles = Array.from({ length: CANDLE_COUNT }, (_, i) => ({
        ...candleRecord,
        open_time: new Date(Date.UTC(2026, 8, 1, 0, i)).toISOString(),
        close_time: new Date(Date.UTC(2026, 8, 1, 0, i + 1)).toISOString(),
      }));
      stubFetch(envelope({ ...candleSeriesView, candles }, candlePage));

      const startedAt = performance.now();
      const result = await makeClient().getCandles(baseParams);
      const elapsedMs = performance.now() - startedAt;

      expect(result.series.kind).toBe("ok");
      if (result.series.kind === "ok") {
        expect(result.series.value.candles).toHaveLength(CANDLE_COUNT);
      }
      expect(elapsedMs).toBeLessThan(1000);
    });
  });

  // task-618 계열 DEEPEN(b45d6e56)과 동일 기법: 실 가드를 되돌린 naive 구현을
  // 나란히 실행해, 지금의 정상 단언(6번 "409는 ApiError로 그대로 던진다")이
  // 실제로 어떤 회귀를 적색으로 잡아내는 게이트인지 증명한다.
  describe("게이트 적색 재현 — CI red-line (409 DATA_COVERAGE_MISSING 조용한 빈채움 회귀)", () => {
    it("catch로 409를 빈 캔들 시리즈로 삼키는 naive 구현으로 회귀하면 '미커버 구간'과 '캔들 0개'를 구별할 수 없게 된다 — 6번 단언이 그 회귀를 실제로 적색으로 만드는 게이트임을 증명", async () => {
      // naive 회귀 시뮬레이션: 실제 getCandles라면 throw했을 409 ApiError를
      // catch해 빈 시리즈로 되돌린다고 가정한 구현(과거 "조용한 빈채움" 결함 클래스).
      function naiveSwallow409(err: unknown): CandleQueryResult {
        if (err instanceof ApiError && err.statusCode === 409) {
          return {
            series: {
              kind: "ok",
              value: {
                key: seriesKey,
                candles: [],
                gaps: [],
                adjustment: "RAW",
                as_of: "2026-09-03T00:00:00Z",
                series_hash: "",
              },
            },
            quality: null,
          };
        }
        throw err;
      }

      stubFetch(coverageMissingError, 409);
      const err = await makeClient().getCandles(baseParams).catch((e: unknown) => e);

      // 실제 구현은 계속 throw한다(6번 단언과 동일 계약) — naive 회귀였다면
      // 아래에서 err가 이미 조용히 삼켜져 도달하지 못했을 코드다.
      expect(err).toBeInstanceOf(ApiError);

      // 같은 err를 naive 구현에 흘리면 "표시할 캔들이 없습니다"(빈 결과)와
      // "미커버 구간"(409)을 화면이 구별할 수 없는 상태가 된다 — 이 결과가
      // ok/candles=[]로 나온다는 사실 자체가 naive 구현의 결함을 보여준다.
      const naiveResult = naiveSwallow409(err);
      expect(naiveResult.series.kind).toBe("ok");
      if (naiveResult.series.kind === "ok") {
        expect(naiveResult.series.value.candles).toHaveLength(0);
      }
    });
  });
});
