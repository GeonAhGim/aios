import { afterEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient, ApiError } from "./client";
import { configureUnauthorizedHandler } from "./http";
import { configureMfaStepUpHandler, requestMfaStepUp } from "./mfaStepUp";
import { configureTokenRefreshHandler } from "./tokenRefresh";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(): AiosApiClient {
  return new AiosApiClient("https://api.example.test", () => null);
}

function forbiddenBody(errorCode: string, traceId: string) {
  return {
    error_code: errorCode,
    message: "추가 인증이 필요합니다.",
    details: {},
    trace_id: traceId,
    retry_after_seconds: null,
  };
}

function expiredBody(traceId: string) {
  return {
    error_code: "AUTH_TOKEN_EXPIRED",
    message: "세션이 만료되었습니다.",
    details: {},
    trace_id: traceId,
    retry_after_seconds: null,
  };
}

// task-481: 모듈 싱글턴(stepUpHandler/inFlightStepUp) 상태가 테스트 간에
// 새지 않도록 매번 초기화한다(tokenRefresh.test.ts와 동일 패턴).
describe("requestMfaStepUp (단위)", () => {
  afterEach(() => {
    configureMfaStepUpHandler(null);
  });

  it("핸들러가 등록되지 않았으면 false로 즉시 해소된다", async () => {
    await expect(requestMfaStepUp()).resolves.toBe(false);
  });

  it("동시 호출은 핸들러를 1회만 실행하고 같은 결과를 공유한다", async () => {
    const handler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(handler);

    const [a, b] = await Promise.all([requestMfaStepUp(), requestMfaStepUp()]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("완료 후 다음 호출은 핸들러를 다시 실행한다", async () => {
    const handler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(handler);

    await requestMfaStepUp();
    await requestMfaStepUp();

    expect(handler).toHaveBeenCalledTimes(2);
  });
});

// task-481: 403 AUTH_MFA_REQUIRED → step-up 핸들러로 TOTP 재인증 → 원요청
// 1회 재시도. classifyForbidden(task-393)이 이미 분류한 "mfa_required"만
// 대상이고, http.ts는 새 403 분류기를 만들지 않는다.
describe("403 AUTH_MFA_REQUIRED step-up 재인증 + 원요청 1회 재시도", () => {
  afterEach(() => {
    configureMfaStepUpHandler(null);
    configureTokenRefreshHandler(null);
    configureUnauthorizedHandler(null);
    vi.unstubAllGlobals();
  });

  it("step-up 성공 시 원요청을 1회 재시도해서 성공 응답을 반환한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(stepUpHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-1")))
      .mockResolvedValueOnce(jsonResponse(200, { total_value: "1000.00", positions: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await makeClient().getPortfolio();

    expect(result).toEqual({ totalValue: "1000.00", positions: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(stepUpHandler).toHaveBeenCalledTimes(1);
  });

  it("negative: 재시도한 원요청이 다시 403을 받으면 재시도 없이 그대로 전파한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(stepUpHandler);

    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-2"))));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 403, errorCode: "AUTH_MFA_REQUIRED" });
    // 최초 요청 + 1회 재시도 = 2번. 두 번째 403은 handleRequestFailure를
    // 다시 거치지 않으므로 step-up 핸들러는 1회만 호출된다.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(stepUpHandler).toHaveBeenCalledTimes(1);
  });

  it("negative: 사용자가 취소(handler가 false 반환)하면 재시도 없이 원 ApiError를 그대로 전파한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(false);
    configureMfaStepUpHandler(stepUpHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-3")));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 403, errorCode: "AUTH_MFA_REQUIRED" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(stepUpHandler).toHaveBeenCalledTimes(1);
  });

  it("negative: AUTH_MFA_INVALID(handler가 false 반환)면 재시도 없이 원 ApiError를 그대로 전파한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(false);
    configureMfaStepUpHandler(stepUpHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-4")));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().getMe();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 403, errorCode: "AUTH_MFA_REQUIRED" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("동시 다발 요청이 403을 받아도 step-up은 1번만 뜨고 모두 같은 결과를 공유한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(stepUpHandler);

    const successBodyByUrl: Record<string, unknown> = {
      "https://api.example.test/portfolio": { total_value: "1000.00", positions: [] },
      "https://api.example.test/users/me": {
        data: {
          user_id: "u-1",
          email: "a@example.com",
          display_name: null,
          mfa_enabled: true,
          status: "active",
          is_verifier: false,
          is_platform_admin: false,
        },
        meta: { trace_id: "trace-me", as_of: "2026-09-03T00:00:00Z", page: null },
      },
    };
    const callCountByUrl = new Map<string, number>();
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const seen = (callCountByUrl.get(url) ?? 0) + 1;
      callCountByUrl.set(url, seen);
      if (seen === 1) {
        return Promise.resolve(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-5")));
      }
      return Promise.resolve(jsonResponse(200, successBodyByUrl[url]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = makeClient();
    const results = await Promise.allSettled([client.getPortfolio(), client.getMe()]);

    expect(results.every((r) => r.status === "fulfilled")).toBe(true);
    expect(stepUpHandler).toHaveBeenCalledTimes(1);
  });

  it("negative: refresh→step-up이 서로를 무한 중첩 호출하지 않는다(재시도한 원요청이 401이면 즉시 전파)", async () => {
    const refreshHandler = vi.fn().mockResolvedValue(true);
    const stepUpHandler = vi.fn().mockResolvedValue(true);
    const unauthorizedHandler = vi.fn();
    configureTokenRefreshHandler(refreshHandler);
    configureMfaStepUpHandler(stepUpHandler);
    configureUnauthorizedHandler(unauthorizedHandler);

    // 1차: 403 MFA_REQUIRED → step-up 성공 → 재시도했더니 이번엔 401
    // AUTH_TOKEN_EXPIRED. 이 재시도는 executeRequest를 직접 호출하므로
    // handleRequestFailure를 다시 거치지 않고 그 자리에서 바로 던져야
    // 한다 — refresh가 다시 걸리며 무한 중첩되면 안 된다.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-6")))
      .mockResolvedValueOnce(jsonResponse(401, expiredBody("trace-7")));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 401, errorCode: "AUTH_TOKEN_EXPIRED" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(stepUpHandler).toHaveBeenCalledTimes(1);
    expect(refreshHandler).not.toHaveBeenCalled();
  });

  it("재시도 요청은 원 요청과 같은 X-Request-Id를 재사용한다", async () => {
    const stepUpHandler = vi.fn().mockResolvedValue(true);
    configureMfaStepUpHandler(stepUpHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(403, forbiddenBody("AUTH_MFA_REQUIRED", "trace-8")))
      .mockResolvedValueOnce(jsonResponse(200, { total_value: "1000.00", positions: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await makeClient().getPortfolio();

    const firstHeaders = new Headers((fetchMock.mock.calls[0] as [string, RequestInit])[1].headers);
    const secondHeaders = new Headers((fetchMock.mock.calls[1] as [string, RequestInit])[1].headers);
    expect(firstHeaders.get("X-Request-Id")).not.toBeNull();
    expect(secondHeaders.get("X-Request-Id")).toBe(firstHeaders.get("X-Request-Id"));
  });
});
