import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ReportsPage } from "./ReportsPage";

let useReportResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn() };

vi.mock("@aios/shared-hooks", () => ({
  useReport: () => useReportResult,
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  useReportResult = { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn() };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  );
}

// task-1155 spec §3.3: 보고서 조회 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 ForbiddenNotice/ErrorMessage 경로로만 보여준다 —
// 이전에는 isLoading이 false가 되면 조용히 빈 화면(report만 없음)이 됐다.
describe("ReportsPage 조회 에러 표시", () => {
  it("정상 응답이면 리포트 통계를 렌더링한다", async () => {
    useReportResult = {
      data: {
        totalReturn: "12.3",
        winRate: "55%",
        maxDrawdown: "8.1",
        tradeCount: 20,
        dailyPnl: [],
        strategyContributions: [],
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("12.3%")).toBeInTheDocument());
  });

  it("negative: 403(AUTHZ_FORBIDDEN) 조회 실패는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    useReportResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw server detail", "trace-1", "AUTHZ_FORBIDDEN"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 조용히 삼켜지지 않고 ErrorMessage 배너로 보여준다", async () => {
    useReportResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("ECONNRESET"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("ECONNRESET")).toBeInTheDocument());
  });
});
