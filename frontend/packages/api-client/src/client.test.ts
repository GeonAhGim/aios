import { afterEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient, ApiError } from "./client";
import { configureUnauthorizedHandler } from "./http";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(): AiosApiClient {
  return new AiosApiClient("https://api.example.test", () => null);
}

describe("AiosApiClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("task-112 봉투가 적용된 엔드포인트(auth)는 data를 unwrap하고 camelCase로 변환한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        data: { access_token: "t-1", token_type: "bearer" },
        meta: { trace_id: "trace-1", as_of: "2026-09-03T00:00:00Z", page: null },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await makeClient().login({ email: "a@example.com", password: "pw" });

    expect(result).toEqual({ accessToken: "t-1", tokenType: "bearer" });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/auth/login");
  });

  it("봉투 에러 응답은 error.message를 실은 ApiError를 던진다", async () => {
    const errorBody = {
      error_code: "STATE_INVALID_TRANSITION",
      message: "이미 처리된 요청입니다.",
      details: {},
      trace_id: "trace-2",
      retry_after_seconds: null,
    };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(409, errorBody)));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().approveMyRequest(1);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 409, message: "이미 처리된 요청입니다." });
  });

  it("봉투가 아직 적용되지 않은 엔드포인트(portfolio)는 body를 그대로 camelCase 변환만 한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { total_value: "1000.00", positions: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await makeClient().getPortfolio();

    expect(result).toEqual({ totalValue: "1000.00", positions: [] });
  });

  // spec §9 PLT-25: GET 계열은 429(RATE_LIMIT_EXCEEDED)의 retry_after_seconds
  // 경과 후 1회 자동 재시도한다.
  it("GET 요청은 429 응답의 retryAfterSec 경과 후 1회 자동 재시도한다", async () => {
    vi.useFakeTimers();
    try {
      const rateLimitedBody = {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다.",
        details: {},
        trace_id: "trace-5",
        retry_after_seconds: 2,
      };
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(429, rateLimitedBody))
        .mockResolvedValueOnce(jsonResponse(200, { total_value: "1000.00", positions: [] }));
      vi.stubGlobal("fetch", fetchMock);

      const resultPromise = makeClient().getPortfolio();
      await vi.advanceTimersByTimeAsync(2000);
      const result = await resultPromise;

      expect(result).toEqual({ totalValue: "1000.00", positions: [] });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  // negative: 금전/POST 계열은 멱등키 규약과 충돌하므로 429여도 절대
  // 자동 재시도하지 않는다 — retryAfterSec은 UI 카운트다운용으로만 노출된다.
  it("POST 요청은 429여도 자동 재시도하지 않는다", async () => {
    const rateLimitedBody = {
      error_code: "RATE_LIMIT_EXCEEDED",
      message: "요청이 너무 많습니다.",
      details: {},
      trace_id: "trace-6",
      retry_after_seconds: 5,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(429, rateLimitedBody));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().approveMyRequest(1);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 429, retryAfterSec: 5 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// task-354: 401 AUTH_* 전역 처리 — http.ts에 주입된 onUnauthorized 훅의 계약.
describe("401 AUTH_* 전역 처리", () => {
  afterEach(() => {
    configureUnauthorizedHandler(null);
  });

  function authErrorBody(errorCode: string, traceId: string) {
    return {
      error_code: errorCode,
      message: "세션이 만료되었습니다.",
      details: {},
      trace_id: traceId,
      retry_after_seconds: null,
    };
  }

  it("401 AUTH_TOKEN_EXPIRED는 onUnauthorized 콜백을 1회 호출한다", async () => {
    const handler = vi.fn();
    configureUnauthorizedHandler(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "trace-401"))),
    );

    let caught: unknown;
    try {
      await makeClient().approveMyRequest(1);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith("AUTH_TOKEN_EXPIRED");
  });

  it("negative: 403 AUTH_TENANT_MISMATCH는 콜백을 호출하지 않는다(로그아웃 대상 아님)", async () => {
    const handler = vi.fn();
    configureUnauthorizedHandler(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(403, authErrorBody("AUTH_TENANT_MISMATCH", "trace-403"))),
    );

    let caught: unknown;
    try {
      await makeClient().approveMyRequest(1);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });

  it("동시에 401이 2건 발생해도 콜백은 1회만 호출된다(중복 리다이렉트 금지)", async () => {
    const handler = vi.fn();
    configureUnauthorizedHandler(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(401, authErrorBody("AUTH_SESSION_REVOKED", "trace-401b"))),
    );

    const client = makeClient();
    const results = await Promise.allSettled([client.approveMyRequest(1), client.rejectMyRequest(2)]);

    expect(results.every((r) => r.status === "rejected")).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
