import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { SessionsPage, type SessionsPageProps } from "./SessionsPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null, logout: vi.fn() }) },
}));

afterEach(cleanup);

function rawSession(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: "session-1",
    createdAt: "2026-09-01T00:00:00Z",
    lastSeenAt: "2026-09-02T00:00:00Z",
    userAgent: "Chrome on Windows",
    ip: "203.0.113.10",
    revokedAt: null,
    ...overrides,
  };
}

function renderPage(props: SessionsPageProps = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/settings/sessions"]}>
        <Routes>
          <Route path="/settings/sessions" element={<SessionsPage {...props} />} />
          <Route path="/login" element={<div>LOGIN_PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}

describe("SessionsPage", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("세션 목록을 렌더링한다", async () => {
    renderPage({
      fetchSessions: async () => [rawSession(), rawSession({ sessionId: "session-2", userAgent: "Safari on macOS" })],
    });

    await waitFor(() => expect(screen.getByText("Chrome on Windows")).toBeInTheDocument());
    expect(screen.getByText("Safari on macOS")).toBeInTheDocument();
  });

  it("현재 세션을 '이 기기'로 표기한다", async () => {
    renderPage({
      fetchSessions: async () => [rawSession(), rawSession({ sessionId: "session-2" })],
      getCurrentSessionId: () => "session-2",
    });

    await waitFor(() => expect(screen.getAllByText("Chrome on Windows")).toHaveLength(2));
    const badges = screen.getAllByText("이 기기");
    expect(badges).toHaveLength(1);
  });

  it("개별 폐기 후 목록을 다시 불러온다", async () => {
    const revoke = vi.fn().mockResolvedValue(undefined);
    const fetchSessions = vi
      .fn()
      .mockResolvedValueOnce([rawSession(), rawSession({ sessionId: "session-2" })])
      .mockResolvedValueOnce([rawSession({ sessionId: "session-2" })]);

    renderPage({
      fetchSessions,
      sessionsClient: { revoke, revokeAll: vi.fn() },
      getCurrentSessionId: () => null,
    });

    await waitFor(() => expect(screen.getAllByRole("button", { name: "폐기" })).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button", { name: "폐기" })[0]);

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("session-1"));
    await waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "폐기" })).toHaveLength(1));
  });

  it("현재 세션을 폐기하면 확인 후 로그인 화면으로 리다이렉트한다", async () => {
    const revoke = vi.fn().mockResolvedValue(undefined);
    renderPage({
      fetchSessions: async () => [rawSession()],
      sessionsClient: { revoke, revokeAll: vi.fn() },
      getCurrentSessionId: () => "session-1",
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "폐기" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "폐기" }));

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("session-1"));
    await waitFor(() => expect(screen.getByText("LOGIN_PAGE")).toBeInTheDocument());
  });

  it("전체 폐기 후 토큰을 정리하고 로그인 화면으로 리다이렉트한다", async () => {
    const revokeAll = vi.fn().mockResolvedValue(undefined);
    renderPage({
      fetchSessions: async () => [rawSession()],
      sessionsClient: { revoke: vi.fn(), revokeAll },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "전체 로그아웃" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "전체 로그아웃" }));

    await waitFor(() => expect(revokeAll).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("LOGIN_PAGE")).toBeInTheDocument());
  });

  it("폐기 API 실패 시 에러를 표시한다", async () => {
    const revoke = vi.fn().mockRejectedValue(new ApiError(500, "일시적인 오류입니다.", "trace-1"));
    renderPage({
      fetchSessions: async () => [rawSession()],
      sessionsClient: { revoke, revokeAll: vi.fn() },
      getCurrentSessionId: () => null,
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "폐기" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "폐기" }));

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("LOGIN_PAGE")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 403(AUTHZ_FORBIDDEN)으로 거부되면 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    renderPage({
      fetchSessions: async () => {
        throw new ApiError(403, "raw server detail", "trace-forbidden-1", "AUTHZ_FORBIDDEN");
      },
    });

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("파싱 실패 항목 5종을 조용히 숨기지 않고 노출한다", async () => {
    const missingSessionId = rawSession();
    delete (missingSessionId as Record<string, unknown>).sessionId;
    const missingCreatedAt = rawSession();
    delete (missingCreatedAt as Record<string, unknown>).createdAt;
    const missingLastSeenAt = rawSession();
    delete (missingLastSeenAt as Record<string, unknown>).lastSeenAt;
    const invalidUserAgent = rawSession({ userAgent: 42 });
    const notARecord = "not-a-session";

    renderPage({
      fetchSessions: async () => [missingSessionId, missingCreatedAt, missingLastSeenAt, invalidUserAgent, notARecord],
    });

    await waitFor(() =>
      expect(screen.getAllByText("세션 정보를 해석할 수 없습니다.")).toHaveLength(5),
    );
  });

  it("fetchSessions가 없으면 기본 구현이 표시 불가 오류를 routeApiError+ErrorMessage 경로로 보여준다", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("세션 목록 조회 API가 아직 제공되지 않습니다.")).toBeInTheDocument());
  });
});
