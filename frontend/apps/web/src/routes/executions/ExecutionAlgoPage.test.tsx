import "../../i18n";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ExecutionAlgoPage } from "./ExecutionAlgoPage";
import type { AlgoProgressResponse } from "@aios/shared-types";
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

const mockAlgoProgress: AlgoProgressResponse = {
  parentId: "550e8400-e29b-41d4-a716-446655440000",
  status: "PENDING",
  totalSlices: 10,
  submittedSlices: 5,
  pendingSlices: 3,
  remainingQty: "500.50",
  demotedToTwap: false,
  demotionReason: null,
};

const mockAlgoProgressDemoted: AlgoProgressResponse = {
  ...mockAlgoProgress,
  demotedToTwap: true,
  demotionReason: "Participation rate exceeded 15%",
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

describe("ExecutionAlgoPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state initially", () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: true,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    expect(screen.getByText("불러오는 중...")).toBeInTheDocument();
  });

  it("renders algo progress data successfully", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: mockAlgoProgress,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText("집행 진행 상태")).toBeInTheDocument();
      expect(screen.getByText("10")).toBeInTheDocument();
      expect(screen.getByText("5")).toBeInTheDocument();
      expect(screen.getByText("3")).toBeInTheDocument();
      expect(screen.getByText("500.50")).toBeInTheDocument();
    });
  });

  it("renders 404 NotFoundState when resource not found", async () => {
    const notFoundError = new Error("Not found");
    (notFoundError as any).statusCode = 404;

    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: notFoundError,
      isError: true,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText("알고리즘 주문이 존재하지 않습니다.")).toBeInTheDocument();
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
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: retryableError,
      isError: true,
      refetch: refetchMock,
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      const retryButton = screen.queryByRole("button", { name: /retry|다시 시도/i });
      expect(retryButton).toBeInTheDocument();
    });
  });

  it("displays demotion warning when demoted to TWAP", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: mockAlgoProgressDemoted,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText("TWAP로 강등됨")).toBeInTheDocument();
      expect(screen.getByText("Participation rate exceeded 15%")).toBeInTheDocument();
    });
  });

  it("shows refresh button and handles click", async () => {
    const refetchMock = vi.fn();
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: mockAlgoProgress,
      isLoading: false,
      error: null,
      isError: false,
      refetch: refetchMock,
      isPending: false,
      isSuccess: true,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "새로고침" })).toBeInTheDocument();
    });

    const refreshButton = screen.getByRole("button", { name: "새로고침" });
    await userEvent.click(refreshButton);

    expect(refetchMock).toHaveBeenCalled();
  });

  it("handles missing parentId gracefully", () => {
    const { render: renderWithWrapper } = renderPage("");

    renderWithWrapper(<ExecutionAlgoPage />);

    expect(screen.getByText("주문 ID가 없습니다.")).toBeInTheDocument();
  });

  it("renders error message on non-retryable error", async () => {
    const fatalError = new Error("Internal server error");
    (fatalError as any).statusCode = 500;

    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: fatalError,
      isError: true,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/Internal server error/i)).toBeInTheDocument();
    });
  });

  it("calculates progress percentage correctly", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: mockAlgoProgress,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText("50.0%")).toBeInTheDocument();
    });
  });
});
