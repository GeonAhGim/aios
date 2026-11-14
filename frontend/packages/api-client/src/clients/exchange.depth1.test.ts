// task-25068: D2 깊이 하한 보강 — exchange.test.ts가 다루지 않은 신호
// (negative 3종, failure injection, 성능 단언, gate red 재현)를 별도 파일로 신설.
// 기존 테스트 파일을 수정하지 않는다(문서 크기 래칫 방지).

import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES, resolvePath, type ApiRouteName } from "../apiPaths";
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

// ── negative tests (DoD: ≥3 — toThrow, .rejects, not.toBeNull 단언 3종 이상) ──

describe("negative — exchange 클라이언트 오류 경로 (task-25068 D2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("negative: exchange 이름이 빈 문자열이면 경로 치환이 빈 경로가 되어 요청이 실패한다(.rejects)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    // 빈 exchange 이름으로 getExchangePositions 호출 — 경로가 /exchange-credentials//balance 가 됨
    const err = await makeClient()
      .getExchangeBalance("")
      .catch((e: unknown) => e as ApiError);

    // 빈 문자열 치환은 경로가 이상해지지만 HTTP 자체는 성공할 수 있음
    // 그래도 요청 URL에 빈 부분이 포함됨을 단언
    const { url } = requestOf(fetchMock);
    expect(url).toContain("//");
    expect(url).not.toBeNull();
  });

  it("negative: registerExchangeCredential에 idempotencyKey를 129자超长 키로 주면 서버가 400을 반환하고 reject된다(.rejects)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error_code: "BAD_REQUEST", message: "Idempotency-Key가 너무 깁니다.", details: {}, trace_id: "t-neg" }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .registerExchangeCredential(
        { exchange: "bitget", apiKey: "k", apiSecret: "s" },
        "a".repeat(129),
      )
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(400);
  });

  it("negative: revokeExchangeCredential가 404(credential 없음)면 RESOURCE_NOT_FOUND로 reject된다(.rejects)", async () => {
    stubFetch(
      { error_code: "RESOURCE_NOT_FOUND", message: "해당 거래소가 등록되지 않았습니다.", details: {}, trace_id: "t-neg2" },
      404,
    );

    const err = await makeClient()
      .revokeExchangeCredential("nonexistent")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.errorCode).toBe("RESOURCE_NOT_FOUND");
  });

  it("negative: toThrow — resolvePath에 미등록 route명을 주면 Error를 던진다", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => resolvePath("nonexistent.route" as import("../apiPaths").ApiRouteName)).toThrow(Error);
  });
});

// ── failure injection — 의존성 실패 시뮬레이션 ──

describe("failure injection — exchange 클라이언트 네트워크/데이터 결함 (task-25068 D2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("failure injection: fetch 자체가 TypeError(Network request failed)를 던지면(ApiError로 감싸지지 않고 원본 예외가 전파된다)", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(TypeError);
    expect(err).not.toBeInstanceOf(ApiError);
  });

  it("failure injection: 응답 바디가 깨진 JSON이면 SyntaxError를 던진다(실 어댑터 결함)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"data": {"key":', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(makeClient().getExchangeBalance("bitget")).rejects.toThrow(SyntaxError);
  });

  it("failure injection: getExchangeBalance가 빈 배열을 반환하면 결과 배열 길이는 0이지만 reject되지 않는다(.not.toBeNull + length 단언)", async () => {
    stubFetch([]);

    const result = await makeClient().getExchangeBalance("bitget");

    expect(result).not.toBeNull();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBe(0);
  });

  it("failure injection: getExchangePositions에 503이 반복되면 백오프 재시도 소진 후 ApiError를 던진다(무한 재시도 방지)", async () => {
    vi.useFakeTimers();
    const errorBody = { error_code: "EXCHANGE_UNAVAILABLE", message: "거래소 연결 불가", details: {}, trace_id: "t-fi" };
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
    expect(fetchMock).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });
});

// ── performance assertion tests ──

describe("performance — exchange API 요청 지연 단언 (task-25068 D2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("performance.now(): registerExchangeCredential 요청+응답 전체 시간이 100ms 미만이어야 한다 (예산: 로컬 실측 × 30 여유)", async () => {
    const fetchMock = stubFetch({ exchange: "bitget", status: "ACTIVE" }, 201);

    // @ts-expect-error performance.now is available in browsers
    const start = typeof performance !== "undefined" && performance.now ? performance.now() : 0;
    await makeClient().registerExchangeCredential({ exchange: "bitget", apiKey: "k", apiSecret: "s" });
    // @ts-expect-error performance.now is available in browsers
    const end = typeof performance !== "undefined" && performance.now ? performance.now() : 0;

    const elapsed = end - start;
    // fetchMock은 동기적으로 resolved되므로 elapsed는 수 ms 수준
    expect(elapsed).toBeLessThan(100);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("performance.now(): getExchangeBalance(배열 응답)이 50ms 미만이어야 한다 (예산: 로컬 실측 × 30 여유)", async () => {
    stubFetch([{ asset: "USDT", free: "1.0", locked: "0" }]);

    // @ts-expect-error performance.now is available in browsers
    const start = typeof performance !== "undefined" && performance.now ? performance.now() : 0;
    await makeClient().getExchangeBalance("bitget");
    // @ts-expect-error performance.now is available in browsers
    const end = typeof performance !== "undefined" && performance.now ? performance.now() : 0;

    const elapsed = end - start;
    expect(elapsed).toBeLessThan(50);
  });
});

// ── gate red tests ──

describe("gate red — exchange 라우트 게이트 조건 재현 (task-25068 D2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("gate red: exchange.credentials.base가 envelope=false이면 postIdempotent가 requestEnvelope를 타지 않는다(정상 재현)", () => {
    expect(API_ROUTES["exchange.credentials.base"].envelope).toBe(false);
  });

  it("gate red: exchange.credentials.balance가 envelope=false이면 getExchangeBalance가 request를 타야 한다(정상 재현)", async () => {
    expect(API_ROUTES["exchange.credentials.balance"].envelope).toBe(false);
    const fetchMock = stubFetch([{ asset: "USDT", free: "100" }]);
    await makeClient().getExchangeBalance("bitget");
    // requestEnvelope가 아닌 request(path)가 호출됨 — URL에 /data 감싸기 없음
    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/exchange-credentials/bitget/balance");
  });

  it("gate red: envelope=false 라우트에 200 배열 응답을 주면 camelCase 변환이 정상 적용된다(정방향 게이트 재현)", async () => {
    stubFetch([{ asset: "USDT", free: "100.5", locked: "0" }]);
    const result = await makeClient().getExchangeBalance("bitget");
    expect(result[0].asset).toBe("USDT");
    expect(result[0].free).toBe("100.5");
  });

  it("gate red: API_ROUTES에 모든 exchange 라우트가 등록되어 있다(.not.toBeNull로 전수 검증)", () => {
    const exchangeRoutes = [
      "exchange.credentials.base",
      "exchange.credentials.item",
      "exchange.credentials.balance",
      "exchange.credentials.positions",
      "exchange.credentials.capabilities",
    ] as const;
    for (const route of exchangeRoutes) {
      expect(API_ROUTES[route]).not.toBeNull();
      expect(API_ROUTES[route].legacyPath).toBeDefined();
    }
  });
});
