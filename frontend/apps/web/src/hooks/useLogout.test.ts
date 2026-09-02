import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLogout } from "./useLogout";

interface LogoutClientOptionsForTest {
  getToken: () => string | null;
  store: { clear: () => void };
}

const logoutMock = vi.fn().mockResolvedValue(undefined);
const logoutAllMock = vi.fn().mockResolvedValue(undefined);
const createLogoutClientMock = vi.fn((_options: LogoutClientOptionsForTest) => ({
  logout: logoutMock,
  logoutAll: logoutAllMock,
}));

vi.mock("@aios/api-client", () => ({
  createLogoutClient: (options: LogoutClientOptionsForTest) => createLogoutClientMock(options),
}));

const authLogoutMock = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useAuthStore: {
    getState: () => ({ token: "tok-1", logout: authLogoutMock }),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient();
  return createElement(QueryClientProvider, { client: qc }, children);
}

describe("useLogout", () => {
  beforeEach(() => {
    logoutMock.mockClear();
    logoutAllMock.mockClear();
    createLogoutClientMock.mockClear();
    authLogoutMock.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("logout()은 api-client의 로그아웃 클라이언트를 통해 서버 요청과 로컬 정리를 위임한다", async () => {
    const { result } = renderHook(() => useLogout(), { wrapper });

    await result.current.logout();

    expect(logoutMock).toHaveBeenCalledTimes(1);
    expect(logoutAllMock).not.toHaveBeenCalled();
  });

  it("logoutAll()은 전체 세션 폐기 경로(logoutAll)를 호출한다", async () => {
    const { result } = renderHook(() => useLogout(), { wrapper });

    await result.current.logoutAll();

    expect(logoutAllMock).toHaveBeenCalledTimes(1);
    expect(logoutMock).not.toHaveBeenCalled();
  });

  it("createLogoutClient에 현재 토큰을 읽는 getToken과 로컬 스토어 정리 콜백을 주입한다", () => {
    renderHook(() => useLogout(), { wrapper });

    expect(createLogoutClientMock).toHaveBeenCalledTimes(1);
    const options = createLogoutClientMock.mock.calls[0][0] as {
      getToken: () => string | null;
      store: { clear: () => void };
    };

    expect(options.getToken()).toBe("tok-1");
    options.store.clear();
    expect(authLogoutMock).toHaveBeenCalledTimes(1);
  });
});
