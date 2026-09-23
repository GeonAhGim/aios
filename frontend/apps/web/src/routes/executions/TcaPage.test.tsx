import "../../i18n";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { TcaPage } from "./TcaPage";
import type { TcaResultResponse } from "@aios/shared-types";
import * as useEmsHooks from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";

const mockRouteParams = vi.hoisted(() => ({
  parentId: "550e8400-e29b-41d4-a716-446655440000" as string,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useParams: () => mockRouteParams,
  };
});

const mockTcaResult: TcaResultResponse = {
  parentId: "550e8400-e29b-41d4-a716-446655440000",
  revision: 1,
  result: {
    arrivalBps: "15.50",
    vwapBps: "-5.25",
    impactBps: "8.75",
    feesBps: "2.00",
    opportunityBps: "10.00",
    schemaVersion: "v1",
  },
  computedAt: "2026-09-23T10:30:00Z",
};

function renderPage(parentId = "550e8400-e29b-41d4-a716-446655440000") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );

  mockRouteParams.parentId = parentId;

  return { queryClient, render: (el: React.ReactElement) => render(el, { wrapper: Wrapper }) };
}

describe("TcaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state initially", () => {
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: true,
      isSuccess: false,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    expect(screen.getByText("불러오는 중...")).toBeInTheDocument();
  });

  it("renders TCA result data successfully", async () => {
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: mockTcaResult,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByText("TCA 분석 결과")).toBeInTheDocument();
      expect(screen.getByText("15.50")).toBeInTheDocument();
      expect(screen.getByText("-5.25")).toBeInTheDocument();
      expect(screen.getByText("8.75")).toBeInTheDocument();
      expect(screen.getByText("2.00")).toBeInTheDocument();
      expect(screen.getByText("10.00")).toBeInTheDocument();
    });
  });

  it("renders EmptyState when TCA not found (404)", async () => {
    const notFoundError = new Error("Not found");
    (notFoundError as any).statusCode = 404;

    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: notFoundError,
      isError: true,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByText("TCA 데이터가 아직 계산되지 않았습니다.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "TCA 계산 시작" })).toBeInTheDocument();
    });
  });

  it("renders error state with retry button on retryable error", async () => {
    const retryableError = new ApiError(
      503,
      "Service temporarily unavailable",
      undefined,
      "DEPENDENCY_NOT_READY",
    );

    const refetchMock = vi.fn();
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: retryableError,
      isError: true,
      refetch: refetchMock,
      isPending: false,
      isSuccess: false,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      const retryButton = screen.queryByRole("button", { name: /retry|다시 시도/i });
      expect(retryButton).toBeInTheDocument();
    });
  });

  it("shows compute form when clicking 다시 계산 button", async () => {
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: mockTcaResult,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "다시 계산" })).toBeInTheDocument();
    });

    const recomputeButton = screen.getByRole("button", { name: "다시 계산" });
    await userEvent.click(recomputeButton);

    await waitFor(() => {
      expect(screen.getByText("TCA 재계산")).toBeInTheDocument();
      expect(screen.getByLabelText("Side")).toBeInTheDocument();
      expect(screen.getByLabelText("Revision")).toBeInTheDocument();
    });
  });

  it("handles missing parentId gracefully", () => {
    const { render: renderWithWrapper } = renderPage("");

    renderWithWrapper(<TcaPage />);

    expect(screen.getByText("주문 ID가 없습니다.")).toBeInTheDocument();
  });

  it("submits compute form and handles error", async () => {
    const mutateAsyncMock = vi.fn().mockRejectedValue(new Error("Compute failed"));

    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: mockTcaResult,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: mutateAsyncMock,
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "다시 계산" })).toBeInTheDocument();
    });

    const recomputeButton = screen.getByRole("button", { name: "다시 계산" });
    await userEvent.click(recomputeButton);

    await waitFor(() => {
      expect(screen.getByText("TCA 재계산")).toBeInTheDocument();
    });

    const submitButton = screen.getByRole("button", { name: "계산" });
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalled();
    });
  });

  it("closes compute form when clicking 취소", async () => {
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: mockTcaResult,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "다시 계산" })).toBeInTheDocument();
    });

    const recomputeButton = screen.getByRole("button", { name: "다시 계산" });
    await userEvent.click(recomputeButton);

    await waitFor(() => {
      expect(screen.getByText("TCA 재계산")).toBeInTheDocument();
    });

    const cancelButton = screen.getByRole("button", { name: "취소" });
    await userEvent.click(cancelButton);

    await waitFor(() => {
      expect(screen.queryByText("TCA 재계산")).not.toBeInTheDocument();
    });
  });

  it("displays revision number in result", async () => {
    vi.spyOn(useEmsHooks, "useLatestTca").mockReturnValue({
      data: mockTcaResult,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    vi.spyOn(useEmsHooks, "useComputeTca").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
      isError: false,
      data: undefined,
      status: "idle",
      reset: vi.fn(),
      mutate: vi.fn(),
      isIdle: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<TcaPage />);

    await waitFor(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });
});
