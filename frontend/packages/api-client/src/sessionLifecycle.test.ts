// §3.4 인증 토큰·세션 + §9 PLT-23/24 교차모듈 통합 회귀(task-1166). tokenStore
// (task-426)·tokenRefresh(task-386/task-1020)·http.ts 401 훅(task-354)·logout
// (task-454)이 각자 유닛 테스트는 초록이어도, 이 파일 이전에는 6개 모듈을 한
// 흐름으로 꿴 테스트가 0건이었다(I-10: "구현됨 ≠ 작동함"). 여기서는 실제
// 프로덕션 공개 API(configureTokenRefreshHandler/configureTokenClearHandler/
// configureUnauthorizedHandler/resetUnauthorizedGuard/createLogoutClient/
// createTokenStore)만 조합하고, 하나의 stub된 전역 fetch 위에서 매 시나리오의
// 호출 횟수·헤더 실값을 단언한다 — 핸들러가 불렸다는 사실만 보는 동어반복 금지.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient, ApiError } from "./client";
import { createAuthTokenRefreshHandler } from "./clients/auth";
import { configureUnauthorizedHandler, resetUnauthorizedGuard } from "./http";
import { configureTokenClearHandler, configureTokenRefreshHandler } from "./tokenRefresh";
import { createTokenStore, type TokenStore } from "./tokenStore";
import { createLogoutClient } from "./logout";
import { resolvePath } from "./apiPaths";

const BASE_URL = "https://api.example.test";
// task-1324: apiPaths.ts에 auth.refresh가 등록되고 clients/auth.ts가 실제
// TokenRefreshHandler(createAuthTokenRefreshHandler)를 제공하므로, 더 이상
// 이 테스트만의 시뮬레이션 핸들러를 손으로 만들지 않는다 — 실제 프로덕션
// 배선을 그대로 조합한다(파일 상단 docstring의 "실제 공개 API만 조합" 원칙).
const PORTFOLIO_URL = `${BASE_URL}${resolvePath("portfolio.get")}`;
const ME_URL = `${BASE_URL}${resolvePath("auth.me")}`;
const WALLET_URL = `${BASE_URL}${resolvePath("wallet.balance")}`;
const REFRESH_URL = `${BASE_URL}${resolvePath("auth.refresh")}`;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function authErrorBody(errorCode: string, traceId: string) {
  return { error_code: errorCode, message: "세션 오류", details: {}, trace_id: traceId, retry_after_seconds: null };
}

function meEnvelopeBody() {
  return {
    data: {
      user_id: "u-1",
      email: "a@example.com",
      display_name: null,
      mfa_enabled: false,
      status: "active",
      is_verifier: false,
      is_platform_admin: false,
    },
    meta: { trace_id: "trace-me", as_of: "2026-09-04T00:00:00Z", page: null },
  };
}

// POST /auth/refresh는 다른 라우터와 동일하게 ApiResponse 봉투를 쓴다(src/api/
// routers/auth.py::refresh가 ok(pair)를 반환) — data/meta 없이 필드를 최상위에
// 두면 unwrap()이 EnvelopeFormatError로 거부한다.
function newPairBody() {
  return {
    data: {
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
      expires_in: 900,
      session_id: "session-0",
    },
    meta: { trace_id: "trace-refresh", as_of: "2026-09-04T00:00:00Z", page: null },
  };
}

function headersOfCall(fetchMock: ReturnType<typeof vi.fn>, index: number): Headers {
  const [, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return new Headers(init.headers);
}

// 마이크로태스크 큐만 반복해서 비운다(타이머·sleep 없음, task-409/423 선례) —
// REFRESH_URL 응답을 일부러 미해결로 붙잡아 둔 상태에서 3개의 동시 401 흐름이
// 각자 refreshAccessToken()까지 실제로 도달하게 만드는 결정론적 방법이다.
async function flushMicrotasks(times = 20): Promise<void> {
  for (let i = 0; i < times; i++) await Promise.resolve();
}

function makeRefreshHandler(store: TokenStore) {
  return createAuthTokenRefreshHandler({ baseUrl: BASE_URL, store });
}

describe("§3.4 세션 수명주기 교차모듈 통합 회귀", () => {
  let tokenStore: TokenStore;
  let client: AiosApiClient;

  beforeEach(() => {
    tokenStore = createTokenStore(); // 생성 시 자신의 clear()를 TokenClearHandler로 등록(task-1020 배선)
    tokenStore.setPair({
      access_token: "access-0",
      refresh_token: "refresh-0",
      token_type: "bearer",
      expires_in: 900,
      session_id: "session-0",
    });
    client = new AiosApiClient(BASE_URL, () => tokenStore.getAccess());
  });

  afterEach(() => {
    // 누수된 전역 훅이 다음 테스트를 초록으로 만드는 것이 이 파일에서 가장
    // 위험한 오탐이다 — 매 테스트 뒤 반드시 전부 초기화한다.
    configureTokenRefreshHandler(null);
    configureTokenClearHandler(null);
    configureUnauthorizedHandler(null);
    resetUnauthorizedGuard();
    vi.unstubAllGlobals();
  });

  it("(1) 401 AUTH_TOKEN_EXPIRED → refresh 1회 → 원요청 1회 재시도 → 성공", async () => {
    const refreshHandler = vi.fn(makeRefreshHandler(tokenStore));
    configureTokenRefreshHandler(refreshHandler);
    const unauthorizedHandler = vi.fn();
    configureUnauthorizedHandler(unauthorizedHandler);

    let portfolioCalls = 0;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === PORTFOLIO_URL) {
        portfolioCalls += 1;
        if (portfolioCalls === 1) return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "t1")));
        return Promise.resolve(jsonResponse(200, { total_value: "1000.00", positions: [] }));
      }
      if (url === REFRESH_URL) return Promise.resolve(jsonResponse(200, newPairBody()));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await client.getPortfolio();

    expect(result).toEqual({ totalValue: "1000.00", positions: [] });
    expect(fetchMock).toHaveBeenCalledTimes(3); // portfolio(401) → refresh(200) → portfolio retry(200)
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
    // 재시도 요청은 refresh가 회전시킨 새 토큰(access-1)을 실었다 — 헤더 실값 확인.
    expect(headersOfCall(fetchMock, 2).get("Authorization")).toBe("Bearer access-1");
  });

  it("(2) 재시도 후 또 401 → 재시도가 2회로 늘지 않고 unauthorized 핸들러가 정확히 1회 호출된다", async () => {
    const refreshHandler = vi.fn(makeRefreshHandler(tokenStore));
    configureTokenRefreshHandler(refreshHandler);
    const unauthorizedHandler = vi.fn();
    configureUnauthorizedHandler(unauthorizedHandler);

    let portfolioCalls = 0;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === PORTFOLIO_URL) {
        portfolioCalls += 1;
        return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", `t-${portfolioCalls}`)));
      }
      if (url === REFRESH_URL) return Promise.resolve(jsonResponse(200, newPairBody()));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await client.getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).errorCode).toBe("AUTH_TOKEN_EXPIRED");
    expect(portfolioCalls).toBe(2); // 원요청 + 재시도 1회. 재재시도는 없다(무한루프 방지, task-386)
    expect(fetchMock).toHaveBeenCalledTimes(3); // portfolio(401) + refresh(200) + portfolio retry(401)
    expect(refreshHandler).toHaveBeenCalledTimes(1); // 재시도가 또 401이어도 refresh를 다시 부르지 않는다
    expect(unauthorizedHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).toHaveBeenCalledWith("AUTH_TOKEN_EXPIRED");
    // 재시도 요청도 회전된 토큰(access-1)을 실었다 — 헤더 실값 확인(동어반복 금지)
    expect(headersOfCall(fetchMock, 2).get("Authorization")).toBe("Bearer access-1");
  });

  it("(3) refresh 응답이 401 AUTH_SESSION_REVOKED → 토큰 전량 폐기 + 리다이렉트, refresh 재시도 없음(시나리오 1과 다른 결말)", async () => {
    const refreshHandler = vi.fn(makeRefreshHandler(tokenStore));
    configureTokenRefreshHandler(refreshHandler);
    const unauthorizedHandler = vi.fn();
    configureUnauthorizedHandler(unauthorizedHandler);

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === PORTFOLIO_URL) return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", "t1")));
      // 회전 재사용 감지: refresh 자체가 401 AUTH_SESSION_REVOKED로 응답한다
      // (AUTH_TOKEN_EXPIRED=갱신 가능과 달리 AUTH_SESSION_REVOKED=갱신 불가·전량 폐기).
      if (url === REFRESH_URL) return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_SESSION_REVOKED", "t2")));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await client.getPortfolio();
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    // 원요청의 401은 AUTH_TOKEN_EXPIRED 그대로 던져진다(task-386 decision) — 하지만
    // 시나리오 1과 달리 재시도가 전혀 일어나지 않고 토큰이 전량 폐기된다는 점이 갈린다.
    expect((caught as ApiError).errorCode).toBe("AUTH_TOKEN_EXPIRED");
    expect(fetchMock).toHaveBeenCalledTimes(2); // portfolio(401) + refresh(401). 재시도(원요청 2번째) 없음
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(tokenStore.getAccess()).toBeNull();
    expect(tokenStore.getRefresh()).toBeNull();
    expect(tokenStore.peekSessionId()).toBeNull();
    expect(unauthorizedHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).toHaveBeenCalledWith("AUTH_TOKEN_EXPIRED");
  });

  it("(4) 동시 401 3건 → refresh 네트워크 호출이 정확히 1회(single-flight)", async () => {
    const refreshHandler = vi.fn(makeRefreshHandler(tokenStore));
    configureTokenRefreshHandler(refreshHandler);
    const unauthorizedHandler = vi.fn();
    configureUnauthorizedHandler(unauthorizedHandler);

    let resolveRefresh: (res: Response) => void = () => {};
    const pendingRefresh = new Promise<Response>((resolve) => {
      resolveRefresh = resolve;
    });
    let refreshFetchCalls = 0;
    const seenPerUrl = new Map<string, number>();

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === REFRESH_URL) {
        refreshFetchCalls += 1;
        return pendingRefresh; // 미해결 Promise로 실제 동시성을 만든다(sleep/timer 금지)
      }
      const seen = (seenPerUrl.get(url) ?? 0) + 1;
      seenPerUrl.set(url, seen);
      if (seen === 1) return Promise.resolve(jsonResponse(401, authErrorBody("AUTH_TOKEN_EXPIRED", `t-${url}`)));
      if (url === PORTFOLIO_URL) return Promise.resolve(jsonResponse(200, { total_value: "1000.00", positions: [] }));
      if (url === ME_URL) return Promise.resolve(jsonResponse(200, meEnvelopeBody()));
      if (url === WALLET_URL) return Promise.resolve(jsonResponse(200, { user_id: "u-1", balance: "500.00" }));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const resultsPromise = Promise.all([client.getPortfolio(), client.getMe(), client.getWalletBalance()]);
    await flushMicrotasks();

    // refresh가 아직 미해결인 상태에서도 3건의 동시 401이 이미 refreshAccessToken()에
    // 도달했다 — 그런데도 실제 네트워크 호출은 1회로 수렴했는지를 여기서 확정한다.
    expect(refreshFetchCalls).toBe(1);

    resolveRefresh(jsonResponse(200, newPairBody()));
    const results = await resultsPromise;

    expect(results[0]).toEqual({ totalValue: "1000.00", positions: [] });
    expect(refreshFetchCalls).toBe(1);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
    // 3개 엔드포인트 모두 재시도에서 회전된 토큰(access-1)을 실었다.
    for (const url of [PORTFOLIO_URL, ME_URL, WALLET_URL]) {
      const retryCall = fetchMock.mock.calls.find(
        ([callUrl, init]) => callUrl === url && new Headers((init as RequestInit).headers).get("Authorization") === "Bearer access-1",
      );
      expect(retryCall, `${url} retry with rotated token`).toBeDefined();
    }
  });

  it("(5) logout 이후 요청에 Authorization 헤더가 붙지 않는다", async () => {
    const logoutClient = createLogoutClient({ baseUrl: BASE_URL, getToken: () => tokenStore.getAccess(), store: tokenStore });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // POST /auth/logout(best-effort)
      .mockResolvedValueOnce(jsonResponse(200, { total_value: "1000.00", positions: [] })); // 로그아웃 후 GET
    vi.stubGlobal("fetch", fetchMock);

    await logoutClient.logout();
    // 로그아웃 요청 자체는 로그아웃 직전 시점의 토큰을 실었다(cleanup 이전 getToken() 호출).
    expect(headersOfCall(fetchMock, 0).get("Authorization")).toBe("Bearer access-0");
    expect(tokenStore.getAccess()).toBeNull();

    await client.getPortfolio();

    expect(headersOfCall(fetchMock, 1).has("Authorization")).toBe(false);
  });
});
