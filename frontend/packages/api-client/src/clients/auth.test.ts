import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { createTokenStore } from "../tokenStore";
import { createAuthTokenRefreshHandler, TokenPairFormatError, withAuth } from "./auth";

class AuthTestClient extends withAuth(ApiClientBase) {}

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

function makeClient(): AuthTestClient {
  return new AuthTestClient("https://api.example.test", () => null);
}

// src/services/auth/tokens.py::TokenPairResponse 1:1 — §3.4 계약.
function tokenPairData(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    access_token: "access-1",
    refresh_token: "refresh-1",
    token_type: "bearer",
    expires_in: 900,
    session_id: "session-1",
    ...overrides,
  };
}

function envelope(data: unknown) {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-04T00:00:00Z", page: null } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// task-1324: PLT-24(task-1075, e0eb498) 병합으로 서버가 login/register 모두
// ApiResponse[TokenPairResponse]를 반환한다 — 예전 TokenResponse(accessToken/
// tokenType만)로는 더 이상 계약을 표현할 수 없다.
describe("withAuth — login/register §3.4 TokenPairResponse", () => {
  it("login 성공 시 §3.4 TokenPairResponse를 camelCase ParsedTokenPair로 돌려준다", async () => {
    const fetchMock = stubFetch(envelope(tokenPairData()));

    const result = await makeClient().login({ email: "a@example.com", password: "pw" });

    expect(result).toEqual({
      accessToken: "access-1",
      refreshToken: "refresh-1",
      expiresIn: 900,
      sessionId: "session-1",
    });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/auth/login");
  });

  it("register도 login과 동일하게 TokenPairResponse를 파싱한다(src/api/routers/auth.py::register가 issue_token_pair 재사용)", async () => {
    stubFetch(envelope(tokenPairData({ access_token: "access-reg" })));

    const result = await makeClient().register({ email: "a@example.com", password: "pw123456789012" });

    expect(result.accessToken).toBe("access-reg");
  });

  // negative(DoD 1): refresh_token 누락 응답 → parseTokenPair가 null을 내고,
  // login()은 그 실패를 삼키지 않고 TokenPairFormatError로 표면화한다(무응답
  // 은폐 금지 — 누락된 refresh_token으로 "성공"을 가장하지 않는다).
  it("negative: refresh_token이 누락된 응답은 조용히 성공 처리하지 않고 TokenPairFormatError를 던진다", async () => {
    const { refresh_token, ...withoutRefreshToken } = tokenPairData();
    stubFetch(envelope(withoutRefreshToken));

    await expect(makeClient().login({ email: "a@example.com", password: "pw" })).rejects.toBeInstanceOf(
      TokenPairFormatError,
    );
  });

  it("negative: token_type이 'bearer'가 아닌 응답도 TokenPairFormatError로 거부한다", async () => {
    stubFetch(envelope(tokenPairData({ token_type: "mac" })));

    await expect(makeClient().login({ email: "a@example.com", password: "pw" })).rejects.toBeInstanceOf(
      TokenPairFormatError,
    );
  });
});

function makeStore() {
  return createTokenStore();
}

// task-1324: POST /auth/refresh 실행부. tokenRefresh.ts(task-386/1020/1166)의
// TokenRefreshHandler 계약대로 configureTokenRefreshHandler에 등록해 쓴다 —
// 여기서는 그 계약(성공→true+저장, 실패→false, throw 없음)만 단위로 검증한다.
describe("createAuthTokenRefreshHandler", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("정상 응답이면 POST /auth/refresh를 호출하고 store에 회전된 쌍을 저장한 뒤 true를 낸다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    const fetchMock = stubFetch(envelope(tokenPairData({ access_token: "access-2", refresh_token: "refresh-2" })));
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(true);

    expect(store.getAccess()).toBe("access-2");
    expect(store.getRefresh()).toBe("refresh-2");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/auth/refresh");
    expect(JSON.parse(init.body as string)).toEqual({ session_id: "session-1", refresh_token: "refresh-1" });
  });

  // negative(DoD 3): logout(tokenStore.clear()) 이후에는 sessionId/refreshToken이
  // 모두 사라지므로, 예약돼 있던 선제 갱신이 뒤늦게 쏘더라도(혹은 수동 호출도)
  // 네트워크 호출 없이 즉시 false로 끝나야 한다 — 정리 직후 토큰이 되살아나는
  // 경합을 막는다(logout.ts task-1020 decision과 동일 취지).
  it("negative: logout으로 tokenStore가 비워진 뒤에는 네트워크 호출 없이 false를 반환한다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    store.clear();
    const fetchMock = stubFetch(envelope(tokenPairData()));
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // negative(DoD 2): refresh 요청 자체가 401(AUTH_TOKEN_EXPIRED 포함)이면 재시도
  // 없이 그 자리에서 false로 끝난다 — 이 핸들러가 스스로 재호출하면 tokenRefresh.ts
  // 의 single-flight in-flight 프라미스를 자기 자신이 기다리는 교착이 생긴다.
  it("negative: refresh 응답이 401이면 재시도 없이 1회 호출로 false를 반환한다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    const fetchMock = stubFetch(
      { error_code: "AUTH_TOKEN_EXPIRED", message: "만료", details: {}, trace_id: "t1", retry_after_seconds: null },
      401,
    );
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // negative(DoD 4): 봉투(data/meta) 형식이 아닌 응답은 §3.3 계약 위반이므로
  // "일단 저장"하지 않고 거부한다.
  it("negative: ApiResponse 봉투가 아닌 응답(data/meta 없음)은 거부하고 false를 반환한다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    const fetchMock = stubFetch(tokenPairData()); // 봉투 없이 바로 필드가 최상위에 있음
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.getAccess()).toBe("access-1"); // 기존 값을 덮어쓰지 않았다
  });

  // negative(DoD 1, refresh 경로): 봉투는 맞지만 data에 refresh_token이 없으면
  // store.setPair가 내부에서 parseTokenPair로 null 판정하고, 그 null을 그대로
  // 삼켜 true로 보고하지 않는다.
  it("negative: 봉투는 정상이지만 refresh_token이 누락된 data는 저장하지 않고 false를 반환한다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    const { refresh_token, ...withoutRefreshToken } = tokenPairData();
    stubFetch(envelope(withoutRefreshToken));
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
    expect(store.getRefresh()).toBe("refresh-1"); // 기존 값을 덮어쓰지 않았다
  });

  it("sessionId 또는 refreshToken이 없으면 네트워크 호출 없이 false를 반환한다", async () => {
    const store = makeStore();
    const fetchMock = stubFetch(envelope(tokenPairData()));
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("네트워크 오류(fetch reject)여도 throw하지 않고 false를 반환한다", async () => {
    const store = makeStore();
    store.setPair(tokenPairData());
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network error")));
    const handler = createAuthTokenRefreshHandler({ baseUrl: "https://api.example.test", store });

    await expect(handler()).resolves.toBe(false);
  });
});
