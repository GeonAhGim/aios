import { afterEach, describe, expect, it, vi } from "vitest";
import { createLogoutClient } from "./logout";
import { configureTokenRefreshHandler, refreshAccessToken } from "./tokenRefresh";

function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeStore() {
  return { clear: vi.fn() };
}

// spec §3.4 + §9 PLT-24 decision: 서버 /auth/logout·/auth/logout-all은
// 미구현이라 200 이외의 모든 결과(4xx/5xx/네트워크 오류)도 "로컬 정리
// 성공"으로 취급해야 한다 — 감사가 지적한 클라이언트 쪽 "로그아웃 no-op"
// 재발을 막는 것이 이 테스트의 목적이다.
describe("createLogoutClient", () => {
  afterEach(() => {
    configureTokenRefreshHandler(null);
    vi.unstubAllGlobals();
  });

  it("정상 응답(200)이면 서버 요청 후 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await client.logout();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/auth/logout");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("5xx 응답이어도 예외를 던지지 않고 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(500, { error_code: "INTERNAL_ERROR" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await expect(client.logout()).resolves.toBeUndefined();
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("401(만료된 토큰으로 로그아웃 시도)이어도 예외 없이 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { error_code: "AUTH_TOKEN_EXPIRED" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await expect(client.logout()).resolves.toBeUndefined();
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("404·501(PLT-24 미구현)이어도 예외 없이 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(501));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await expect(client.logout()).resolves.toBeUndefined();
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("네트워크 오류(fetch reject)여도 예외 없이 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network error"));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await expect(client.logout()).resolves.toBeUndefined();
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("logoutAll()은 /auth/logout-all을 호출하고 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await client.logoutAll();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/auth/logout-all");
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("동시 2회 logout() 호출은 서버 요청 1회만 보내고 같은 결과를 공유한다", async () => {
    let resolveFetch: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    const first = client.logout();
    const second = client.logout();
    resolveFetch!(jsonResponse(200, { status: "ok" }));
    await Promise.all([first, second]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it("logout() 완료 후 재호출하면 서버 요청을 다시 보낸다(single-flight는 진행 중일 때만)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    await client.logout();
    await client.logout();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(store.clear).toHaveBeenCalledTimes(2);
  });

  it("logout() 이후 진행 중이던 자동 refresh 핸들러를 해제해 정리 직후 토큰이 되살아나지 않게 한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const store = makeStore();
    const client = createLogoutClient({ baseUrl: "https://api.example.test", getToken: () => "tok", store });

    const refreshHandler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(refreshHandler);

    await client.logout();

    // 정리 이후의 refreshAccessToken() 호출은 핸들러가 해제되어 즉시
    // false로 해소되고, 더 이상 handler를 실행하지 않는다.
    await expect(refreshAccessToken()).resolves.toBe(false);
    expect(refreshHandler).not.toHaveBeenCalled();
  });
});
