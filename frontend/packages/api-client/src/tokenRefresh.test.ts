import { afterEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient, ApiError } from "./client";
import { configureUnauthorizedHandler } from "./http";
import { configureTokenClearHandler, configureTokenRefreshHandler, refreshAccessToken } from "./tokenRefresh";
import { createTokenStore } from "./tokenStore";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(): AiosApiClient {
  return new AiosApiClient("https://api.example.test", () => null);
}

function authErrorBody(errorCode: string, traceId: string) {
  return {
    error_code: errorCode,
    message: "세션이 만료되었습니다.",
    details: {},
    trace_id: traceId,
    retry_after_seconds: null,
  };
}

// task-386: 모듈 싱글턴(refreshHandler/inFlightRefresh) 상태가 테스트 간에
// 새지 않도록 매번 초기화한다.
describe("refreshAccessToken (단위)", () => {
  afterEach(() => {
    configureTokenRefreshHandler(null);
    vi.unstubAllGlobals();
  });

  it("핸들러가 등록되지 않았으면 false로 즉시 해소된다", async () => {
    await expect(refreshAccessToken()).resolves.toBe(false);
  });

  it("동시 호출은 핸들러를 1회만 실행하고 같은 결과를 공유한다", async () => {
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);

    const [a, b] = await Promise.all([refreshAccessToken(), refreshAccessToken()]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("완료 후 다음 호출은 핸들러를 다시 실행한다", async () => {
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);

    await refreshAccessToken();
    await refreshAccessToken();

    expect(handler).toHaveBeenCalledTimes(2);
  });
});

// task-386: AUTH_TOKEN_EXPIRED만 refresh 후 원요청 1회 재시도. AUTH_TOKEN_INVALID/
// AUTH_SESSION_REVOKED는 refresh를 시도하지 않고 즉시 로그아웃 알림으로 넘어간다.
describe("401 AUTH_TOKEN_EXPIRED 자동 refresh + 원요청 1회 재시도", () => {
  afterEach(() => {
    configureTokenRefreshHandler(null);
    configureUnauthorizedHandler(null);
    vi.unstubAllGlobals();
  });

  it("refresh 성공 시 원요청을 1회 재시도해서 성공 응답을 반환한다", async () => {
    const refreshHandler = vi.fn().mockResolvedValue(true);
    const unauthorizedHandler = vi.fn();
    configureTokenRefreshHandler(refreshHandler);
    configureUnauthorizedHandler(unauthorizedHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "trace-1")))
      .mockResolvedValueOnce(jsonResponse(200, { total_value: "1000.00", positions: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await makeClient().getPortfolio();

    expect(result).toEqual({ totalValue: "1000.00", positions: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
  });

  it("refresh 실패 시 재시도하지 않고 로그아웃 알림 후 원본 에러를 던진다", async () => {
    const refreshHandler = vi.fn().mockResolvedValue(false);
    const unauthorizedHandler = vi.fn();
    configureTokenRefreshHandler(refreshHandler);
    configureUnauthorizedHandler(unauthorizedHandler);

    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "trace-2")));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await makeClient().getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ statusCode: 401, errorCode: "AUTH_TOKEN_EXPIRED" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).toHaveBeenCalledWith("AUTH_TOKEN_EXPIRED");
  });

  it.each(["AUTH_TOKEN_INVALID", "AUTH_SESSION_REVOKED"])(
    "negative: %s는 refresh를 시도하지 않고 즉시 로그아웃 알림으로 넘어간다",
    async (errorCode) => {
      const refreshHandler = vi.fn().mockResolvedValue(true);
      const unauthorizedHandler = vi.fn();
      configureTokenRefreshHandler(refreshHandler);
      configureUnauthorizedHandler(unauthorizedHandler);

      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(401, authErrorBody(errorCode, "trace-3")));
      vi.stubGlobal("fetch", fetchMock);

      let caught: unknown;
      try {
        await makeClient().getPortfolio();
      } catch (e) {
        caught = e;
      }

      expect(caught).toBeInstanceOf(ApiError);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(refreshHandler).not.toHaveBeenCalled();
      expect(unauthorizedHandler).toHaveBeenCalledTimes(1);
      expect(unauthorizedHandler).toHaveBeenCalledWith(errorCode);
    },
  );

  it("동시에 2건이 AUTH_TOKEN_EXPIRED를 받아도 refresh는 1회만 호출되고 둘 다 재시도로 복구된다", async () => {
    const refreshHandler = vi.fn().mockResolvedValue(true);
    const unauthorizedHandler = vi.fn();
    configureTokenRefreshHandler(refreshHandler);
    configureUnauthorizedHandler(unauthorizedHandler);

    const successBodyByUrl: Record<string, unknown> = {
      "https://api.example.test/portfolio": { total_value: "1000.00", positions: [] },
      "https://api.example.test/users/me": {
        data: {
          user_id: "u-1",
          email: "a@example.com",
          display_name: null,
          mfa_enabled: false,
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
        return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "trace-4")));
      }
      return Promise.resolve(jsonResponse(200, successBodyByUrl[url]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = makeClient();
    const results = await Promise.allSettled([client.getPortfolio(), client.getMe()]);

    expect(results.every((r) => r.status === "fulfilled")).toBe(true);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
  });
});

// task-1020(§3.4/§9 PLT-23): refresh 회전 재사용 감지·기타 401/403 실패 시
// tokenStore 전량 폐기. configureTokenClearHandler는 tokenStore.ts가 자신의
// clear()를 등록하는 훅이다 — 여기서는 그 훅 자체와, 실제 createTokenStore()로
// 만든 스토어가 등록·폐기되는 배선을 함께 검증한다.
describe("refreshAccessToken 실패 시 등록된 clearHandler 호출(tokenStore 전량 폐기)", () => {
  afterEach(() => {
    configureTokenRefreshHandler(null);
    configureTokenClearHandler(null);
    vi.unstubAllGlobals();
  });

  it("refresh 실패 시 등록된 clearHandler를 1회 호출한다", async () => {
    configureTokenRefreshHandler(vi.fn().mockResolvedValue(false));
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    await expect(refreshAccessToken()).resolves.toBe(false);

    expect(clearHandler).toHaveBeenCalledTimes(1);
  });

  it("refresh 성공 시 clearHandler를 호출하지 않는다", async () => {
    configureTokenRefreshHandler(vi.fn().mockResolvedValue(true));
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    await expect(refreshAccessToken()).resolves.toBe(true);

    expect(clearHandler).not.toHaveBeenCalled();
  });

  it("동시에 대기 중인 모든 호출이 같은 실패로 끝나고 clearHandler는 1회만 호출된다(재시도 없음)", async () => {
    let resolveHandler: (value: boolean) => void = () => {};
    const refreshHandler = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveHandler = resolve;
        }),
    );
    configureTokenRefreshHandler(refreshHandler);
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    const waiters = [refreshAccessToken(), refreshAccessToken(), refreshAccessToken()];
    resolveHandler(false);
    const results = await Promise.all(waiters);

    expect(results).toEqual([false, false, false]);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(clearHandler).toHaveBeenCalledTimes(1);
  });

  // task-1380(§3.4/§9 PLT-23, task-1373 REJECT 후속): 주입된 핸들러가 계약을
  // 어기고 reject/throw해도(예: catch 없이 전파된 네트워크 예외) clearHandler는
  // 반드시 호출되어야 하고, refreshAccessToken() 자체는 절대 reject하면 안
  // 된다 — tokenStore.ts의 `void refreshAccessToken()`(선제 갱신) 같은
  // catch-없는 호출부에서 unhandled rejection이 나면 안 되기 때문이다.
  it("refresh 핸들러가 reject하면 clearHandler를 호출하고 false로 resolve한다(reject 우회 금지)", async () => {
    configureTokenRefreshHandler(vi.fn().mockRejectedValue(new Error("network down")));
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    await expect(refreshAccessToken()).resolves.toBe(false);

    expect(clearHandler).toHaveBeenCalledTimes(1);
  });

  it("refresh 핸들러가 동기적으로 throw해도 clearHandler를 호출하고 false로 resolve한다", async () => {
    configureTokenRefreshHandler(
      vi.fn(() => {
        throw new Error("sync boom");
      }),
    );
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    await expect(refreshAccessToken()).resolves.toBe(false);

    expect(clearHandler).toHaveBeenCalledTimes(1);
  });

  it("동시에 대기 중인 모든 호출이 reject 실패를 공유해도 clearHandler는 1회만 호출된다", async () => {
    let rejectHandler: (err: unknown) => void = () => {};
    const refreshHandler = vi.fn(
      () =>
        new Promise<boolean>((_resolve, reject) => {
          rejectHandler = reject;
        }),
    );
    configureTokenRefreshHandler(refreshHandler);
    const clearHandler = vi.fn();
    configureTokenClearHandler(clearHandler);

    const waiters = [refreshAccessToken(), refreshAccessToken(), refreshAccessToken()];
    rejectHandler(new Error("session revoked"));
    const results = await Promise.all(waiters);

    expect(results).toEqual([false, false, false]);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(clearHandler).toHaveBeenCalledTimes(1);
  });

  it("createTokenStore()로 만든 실제 스토어는 refresh 핸들러가 reject해도 스스로 전량 폐기된다", async () => {
    const store = createTokenStore();
    store.setPair({
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
      expires_in: 900,
      session_id: "session-1",
    });
    expect(store.getAccess()).toBe("access-1");

    configureTokenRefreshHandler(vi.fn().mockRejectedValue(new Error("network down")));

    await expect(refreshAccessToken()).resolves.toBe(false);

    expect(store.getAccess()).toBeNull();
    expect(store.getRefresh()).toBeNull();
    expect(store.peekSessionId()).toBeNull();
  });

  it("createTokenStore()로 만든 실제 스토어는 refresh 실패 시 스스로 전량 폐기된다", async () => {
    const store = createTokenStore();
    store.setPair({
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
      expires_in: 900,
      session_id: "session-1",
    });
    expect(store.getAccess()).toBe("access-1");

    configureTokenRefreshHandler(vi.fn().mockResolvedValue(false));

    await expect(refreshAccessToken()).resolves.toBe(false);

    expect(store.getAccess()).toBeNull();
    expect(store.getRefresh()).toBeNull();
    expect(store.peekSessionId()).toBeNull();
  });
});
