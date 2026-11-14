import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withExchange } from "./exchange";

class ExchangeDepthTestClient extends withExchange(ApiClientBase) {}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): ExchangeDepthTestClient {
  return new ExchangeDepthTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
// ── negative tests ───────────────────────────────────────────────────────────
describe("exchange depth1 — negative", () => {
  it("getExchangeCapabilities 500: ApiError로 거부되고 errorCode가 전달된다", async () => {
    stubFetch(
      { error_code: "INTERNAL_ERROR", message: "서버 오류", details: {}, trace_id: "t-n1" },
      500,
    );

    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(500);
    expect(err.errorCode).toBe("INTERNAL_ERROR");
  });

  it("getExchangeBalance 401: AUTH_TOKEN_EXPIRED — ApiError로 거부된다", async () => {
    stubFetch(
      { error_code: "AUTH_TOKEN_EXPIRED", message: "토큰 만료", details: {}, trace_id: "t-n2" },
      401,
    );

    const err = await makeClient()
      .getExchangeBalance("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(401);
    expect(err.errorCode).toBe("AUTH_TOKEN_EXPIRED");
  });

  it("getExchangePositions 422: VALIDATION_FAILED — ApiError로 거부된다", async () => {
    stubFetch(
      { error_code: "VALIDATION_FAILED", message: "잘못된 요청", details: {}, trace_id: "t-n3" },
      422,
    );

    const err = await makeClient()
      .getExchangePositions("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(422);
    expect(err.errorCode).toBe("VALIDATION_FAILED");
  });

  it("registerExchangeCredential: 204 No Content 응답을 받으면 undefined가 반환된다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await makeClient().registerExchangeCredential({
      exchange: "bitget",
      apiKey: "k",
      apiSecret: "s",
    });

    expect(result).toBeUndefined();
  });
});
// ── failure injection tests ──────────────────────────────────────────────────
describe("exchange depth1 — failure injection", () => {
  it("fetch가 network error를 던지면 rejected 된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Network request failed")),
    );

    await expect(makeClient().getExchangeBalance("bitget")).rejects.toThrow("Network request failed");
  });

  it("fetch가 HttpResponse.error를 던지면 rejected 된다", async () => {
    const networkError = new TypeError("fetch failed");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(networkError));

    await expect(makeClient().getExchangeCapabilities("bitget")).rejects.toBeInstanceOf(TypeError);
  });

  it("서버가 비정규 JSON을 반환하면 JSON.parse가 SyntaxError를 던진다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("not-json-at-all", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(makeClient().getExchangeBalance("bitget")).rejects.toThrow(/JSON/);
  });

  it("revokeExchangeCredential: DELETE가 502 Bad Gateway를 반환하면 ApiError로 거부된다", async () => {
    stubFetch(
      { error_code: "BAD_GATEWAY", message: "게이트웨이 오류", details: {}, trace_id: "t-f1" },
      502,
    );

    const err = await makeClient()
      .revokeExchangeCredential("bitget")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(502);
    expect(err.errorCode).toBe("BAD_GATEWAY");
  });
});
// ── performance assertion tests ──────────────────────────────────────────────
describe("exchange depth1 — performance", () => {
  it("performance.now()로 재면 getExchangeBalance 단일 호출 시간이 200ms 미만이어야 한다 (예산: mock 환경 기준 여유)", async () => {
    stubFetch([{ asset: "USDT", free: "1.0", locked: "0" }]);

    const start = typeof performance !== "undefined" ? performance.now() : -1;
    await makeClient().getExchangeBalance("bitget");
    const end = typeof performance !== "undefined" ? performance.now() : -1;

    if (start >= 0 && end >= 0) {
      const elapsed = end - start;
      expect(elapsed).toBeLessThan(200);
    }
  });

  it("performance.now()로 재면 getExchangeCapabilities 호출 시간이 200ms 미만이어야 한다", async () => {
    stubFetch({ symbol: "BTCUSDT", exchange: "bitget", status: "ACTIVE" });

    const start = typeof performance !== "undefined" ? performance.now() : -1;
    await makeClient().getExchangeCapabilities("bitget");
    const end = typeof performance !== "undefined" ? performance.now() : -1;

    if (start >= 0 && end >= 0) {
      const elapsed = end - start;
      expect(elapsed).toBeLessThan(200);
    }
  });
});
// ── gate red tests ───────────────────────────────────────────────────────────

describe("exchange depth1 — gate red", () => {
  it("gate red: exchange 이름에 SQL 인젝션 시도가 들어가면 경로 치환에 그대로 반영된다", async () => {
    const fetchMock = stubFetch(
      { error_code: "NOT_FOUND", message: "없음", details: {}, trace_id: "t-g1" },
      404,
    );

    await makeClient()
      .getExchangeBalance("'; DROP TABLE exchange_credentials; --")
      .catch(() => {});

    const { url } = requestOf(fetchMock);
    expect(url).toContain("'; DROP TABLE");
  });

  it("gate red: getExchangePositions가 빈 배열을 반환하면 downstream이 빈 배열로 처리한다", async () => {
    stubFetch([]);

    const result = await makeClient().getExchangePositions("bitget");

    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBe(0);
  });

  it("gate red: registerExchangeCredential가 409 Conflict를 반환하면 중복 키 오류가 ApiError로 전달된다", async () => {
    stubFetch(
      { error_code: "CONFLICT", message: "중복 키", details: {}, trace_id: "t-g2" },
      409,
    );

    const err = await makeClient()
      .registerExchangeCredential({ exchange: "bitget", apiKey: "k", apiSecret: "s" })
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(409);
    expect(err.errorCode).toBe("CONFLICT");
  });
});
