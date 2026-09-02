import { afterEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient, ApiError } from "./client";

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
});
