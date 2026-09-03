import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PortfolioPage } from "./PortfolioPage";

// task-1057 §3.3 5xx: useMe/useLogout(AppShell 의존)·useRebalancePortfolio(폼 제출,
// 이 화면에서는 쓰지 않음)만 목으로 바꾸고 usePortfolio는 실제 훅(@aios/shared-hooks)
// 그대로 둔다 — usePortfolio가 곧바로 {isError,error,refetch}를 흉내 내는 손조립
// 객체를 넘기면 컴포넌트가 routeApiError로 실제 판정하는지 검증할 수 없는 동어반복이
// 된다. 실제 apiClient가 진짜 fetch를 타도록 fetch만 stub해 응답(상태코드·error_code)
// 으로부터 재시도 가능 여부를 판단한다.
vi.mock("@aios/shared-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/shared-hooks")>();
  return {
    ...actual,
    useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
    useLogout: () => vi.fn(),
    useRebalancePortfolio: () => ({ mutateAsync: vi.fn(), isPending: false, data: undefined }),
  };
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PortfolioPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PortfolioPage 5xx 재시도 배선", () => {
  it("negative: DEPENDENCY_NOT_READY(503)는 api-client 자동 백오프(1s,2s) 소진 뒤에도 실패하면 다시 시도 버튼을 보여준다", async () => {
    vi.useFakeTimers();
    const body = {
      error_code: "DEPENDENCY_NOT_READY",
      message: "raw server detail",
      details: {},
      trace_id: "trace-pf-503",
      retry_after_seconds: null,
    };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(503, body)));
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await act(async () => {
      await vi.runAllTimersAsync();
    });
    vi.useRealTimers();

    await waitFor(() =>
      expect(
        screen.getByText("서비스가 준비 중입니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("negative: EXCHANGE_FATAL(502)은 재시도 없이 즉시 실패하고 다시 시도 버튼을 보여주지 않는다", async () => {
    const body = {
      error_code: "EXCHANGE_FATAL",
      message: "raw server detail",
      details: {},
      trace_id: "trace-pf-502",
      retry_after_seconds: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(502, body));
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("거래소 자격증명을 확인해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
