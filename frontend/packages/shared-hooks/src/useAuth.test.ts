import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLogout } from "./useAuth";
import { useAuthStore } from "./useAuthStore";

// spec §3.4 + §9 PLT-24. 감사 지적("로그아웃 no-op") 재현·회귀 방지. 이 훅은
// task-3323 이전에는 useAuthStore.logout()과 qc.clear()만 호출하고 서버를 전혀
// 부르지 않았다 — 아래 "게이트 재현" 테스트는 이 파일이 고치기 전 구현으로
// 되돌리면 바로 빨갛게 실패하도록(fetch가 한 번도 안 불림) 짜여 있다.
function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient();
  return createElement(QueryClientProvider, { client: qc }, children);
}

describe("useLogout (shared-hooks/useAuth)", () => {
  beforeEach(() => {
    localStorage.setItem("aios_access_token", "tok-1");
    useAuthStore.setState({ token: "tok-1", user: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    useAuthStore.setState({ token: null, user: null });
  });

  it("게이트 재현: 정상 응답(200)이면 실제로 POST /auth/logout을 호출한 뒤 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    await result.current();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/auth\/logout$/);
    expect(init.method).toBe("POST");
    expect(useAuthStore.getState().token).toBeNull();
    expect(localStorage.getItem("aios_access_token")).toBeNull();
  });

  it("negative: 5xx 응답이어도 예외를 던지지 않고 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(500, { error_code: "INTERNAL_ERROR" }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    await expect(result.current()).resolves.toBeUndefined();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("negative: 404·501(PLT-24 미구현)이어도 예외를 던지지 않고 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(501));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    await expect(result.current()).resolves.toBeUndefined();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("failure-injection: 네트워크 오류(fetch reject)여도 예외 없이 로컬 토큰을 정리한다", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network error"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    await expect(result.current()).resolves.toBeUndefined();
    expect(useAuthStore.getState().token).toBeNull();
    expect(localStorage.getItem("aios_access_token")).toBeNull();
  });

  it("negative: 서버 요청이 끝나기 전에는 TanStack Query 캐시를 비우지 않는다(정리 순서)", async () => {
    let resolveFetch: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    const pending = result.current();
    // 아직 fetch가 해소되지 않았으니 store는 그대로다.
    expect(useAuthStore.getState().token).toBe("tok-1");

    resolveFetch!(jsonResponse(200, { status: "ok" }));
    await pending;
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("성능 단언: mock fetch 기준 logout() 1회 왕복은 200ms 미만이다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useLogout(), { wrapper });

    const start = performance.now();
    await result.current();
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(200);
  });

  it("토큰이 정리된 뒤에는 useMe 등 인증 필요 쿼리가 다시 fetch를 트리거하지 않도록 캐시도 함께 비운다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const qc = new QueryClient();
    qc.setQueryData(["me"], { email: "a@example.test" });
    const localWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const { result } = renderHook(() => useLogout(), { wrapper: localWrapper });

    await result.current();

    await waitFor(() => {
      expect(qc.getQueryData(["me"])).toBeUndefined();
    });
  });
});
