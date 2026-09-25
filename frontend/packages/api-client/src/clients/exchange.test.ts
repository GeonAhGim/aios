import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withExchange } from "./exchange";

class ExchangeTestClient extends withExchange(ApiClientBase) {}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): ExchangeTestClient {
  return new ExchangeTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// task-1145 QA: exchange.credentials.base/balance/capabilities는 http.test.ts
// (task-1159)가 envelope=false로 고정했지만 item(:exchange DELETE)은 빠져 있었다.
// src/api/routers/exchange_credentials.py 모듈 docstring(PLT-17 needs_decision)대로
// 네 경로 전부 아직 봉투 미적용이다.
describe("exchange apiPaths 레지스트리: 봉투 미적용 상태를 고정한다", () => {
  it.each([
    "exchange.credentials.base",
    "exchange.credentials.item",
    "exchange.credentials.balance",
    "exchange.credentials.positions",
    "exchange.credentials.capabilities",
  ] as const)("%s: envelope=false", (routeName) => {
    expect(API_ROUTES[routeName].envelope).toBe(false);
  });
});

describe("withExchange", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("registerExchangeCredential: postIdempotent — 키를 넘기지 않으면 16~128자 Idempotency-Key를 자동 생성한다(키 수명주기 불변)", async () => {
    const fetchMock = stubFetch({ exchange: "bitget", status: "ACTIVE" }, 201);

    await makeClient().registerExchangeCredential({ exchange: "bitget", apiKey: "k", apiSecret: "s" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/exchange-credentials");
    const key = new Headers(init.headers).get("Idempotency-Key");
    expect(key).not.toBeNull();
    expect((key as string).length).toBeGreaterThanOrEqual(16);
    expect((key as string).length).toBeLessThanOrEqual(128);
    expect(JSON.parse(init.body as string)).toEqual({ exchange: "bitget", api_key: "k", api_secret: "s" });
  });

  it("revokeExchangeCredential: DELETE /exchange-credentials/:exchange 치환", async () => {
    const fetchMock = stubFetch({ exchange: "bitget", status: "REVOKED" });

    await makeClient().revokeExchangeCredential("bitget");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/exchange-credentials/bitget");
    expect(init.method).toBe("DELETE");
  });

  it("getExchangeBalance: 비봉투 GET — top-level 배열 응답을 그대로 camelCase 변환한다(§3.3 data:[] 감싸기는 /api/v1 전용)", async () => {
    const fetchMock = stubFetch([{ asset: "USDT", free: "1.0", locked: "0" }]);

    const result = await makeClient().getExchangeBalance("bitget");

    expect(requestOf(fetchMock).url).toBe("https://api.example.test/exchange-credentials/bitget/balance");
    expect(Array.isArray(result)).toBe(true);
    expect(result[0].asset).toBe("USDT");
  });

  it("getExchangeCapabilities 404(CredentialNotFoundError→RESOURCE_NOT_FOUND): ApiError로 거부된다", async () => {
    stubFetch({ error_code: "RESOURCE_NOT_FOUND", message: "자격증명이 없습니다.", details: {}, trace_id: "t-1" }, 404);

    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(404);
    expect(err.errorCode).toBe("RESOURCE_NOT_FOUND");
  });

  it("getExchangePositions: 비봉투 GET — top-level 배열 응답을 그대로 camelCase 변환한다", async () => {
    const fetchMock = stubFetch([
      {
        symbol: "BTCUSDT",
        exchange: "bitget",
        strategy_id: "s-1",
        execution_id: null,
        quantity: "0.5",
        average_entry_price: { amount: "60000", currency: "USDT" },
        current_price: { amount: "61000", currency: "USDT" },
        unrealized_pnl: { amount: "500", currency: "USDT" },
        realized_pnl: { amount: "0", currency: "USDT" },
        leverage: "1",
        margin: null,
        entry_time: "2026-09-17T00:00:00+00:00",
        updated_at: "2026-09-17T00:00:00+00:00",
        asset_class: "CRYPTO",
        option_type: null,
        strike_price: null,
        expiry_date: null,
        contract_multiplier: null,
        underlying_symbol: null,
      },
    ]);

    const result = await makeClient().getExchangePositions("bitget");

    expect(requestOf(fetchMock).url).toBe("https://api.example.test/exchange-credentials/bitget/positions");
    expect(Array.isArray(result)).toBe(true);
    expect(result[0].symbol).toBe("BTCUSDT");
    expect(result[0].averageEntryPrice).toEqual({ amount: "60000", currency: "USDT" });
  });

  // task-4002 DoD(a): 자격증명 없으면 404 → routeApiError가 소비할 수 있게 ApiError로
  // 거부된다(RESOURCE_NOT_FOUND, capabilities 404 케이스와 동일 패턴).
  it("getExchangePositions 404(CredentialNotFoundError→RESOURCE_NOT_FOUND): ApiError로 거부된다", async () => {
    stubFetch({ error_code: "RESOURCE_NOT_FOUND", message: "자격증명이 없습니다.", details: {}, trace_id: "t-2" }, 404);

    const err = await makeClient()
      .getExchangePositions("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(404);
    expect(err.errorCode).toBe("RESOURCE_NOT_FOUND");
  });

  // task-4002 DoD(b): 5xx(거래소 장애)는 classifyServerError(serverError.ts)의 재시도
  // 분기 대상인 EXCHANGE_UNAVAILABLE이다 — GET은 withGetRetry(httpErrors.ts)가 1초·2초
  // 백오프로 자동 재시도하므로(marketData.test.ts 선례와 동일 패턴: mockImplementation +
  // fake timers로 매 호출 새 Response를 돌려준다), 재시도를 다 태우고도 여전히 503이면
  // ApiError로 거부되고 fetch가 정확히 3회(최초 1 + 백오프 2) 호출됐는지 확인한다.
  it("getExchangePositions 503(EXCHANGE_UNAVAILABLE) 재시도 소진: 백오프(1초·2초) 후에도 ApiError로 거부된다", async () => {
    vi.useFakeTimers();
    const errorBody = { error_code: "EXCHANGE_UNAVAILABLE", message: "거래소 응답 없음.", details: {}, trace_id: "t-3" };
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(errorBody), { status: 503, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const resultPromise = makeClient()
      .getExchangePositions("bitget")
      .catch((e: unknown) => e as ApiError);
    await vi.advanceTimersByTimeAsync(5_000);
    const err = await resultPromise;

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(503);
    expect(err.errorCode).toBe("EXCHANGE_UNAVAILABLE");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });
});
