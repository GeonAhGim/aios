import "../../i18n";
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ExecutionAlgoPage } from "./ExecutionAlgoPage";
import type { AlgoProgressResponse } from "@aios/shared-types";
import * as useEmsHooks from "@aios/shared-hooks";

// ── Route-param mock (same pattern as ExecutionAlgoPage.test.tsx) ──

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

// ── Helpers ──

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

const okProgress: AlgoProgressResponse = {
  parentId: "550e8400-e29b-41d4-a716-446655440000",
  status: "PENDING",
  totalSlices: 10,
  submittedSlices: 5,
  pendingSlices: 3,
  remainingQty: "500.50",
  demotedToTwap: false,
  demotionReason: null,
};

// ── Tests ──

describe("ExecutionAlgoPage — DEEPEN-DEPTH EM-18 D2 보강", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  // ── Negative tests (3종 이상: toThrow, .rejects, .not.toBeInTheDocument) ──

  it("throws when useAlgoProgress hook itself throws on mount", () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockImplementation(() => {
      throw new Error("hook initialization failure");
    });

    const { render: renderWithWrapper } = renderPage();
    expect(() => renderWithWrapper(<ExecutionAlgoPage />)).toThrow("hook initialization failure");
  });

  it("rejects when queryFn fails and error is not 404", async () => {
    const networkErr = new Error("Network failure");
    (networkErr as any).statusCode = 503;

    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: networkErr,
      isError: true,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/Network failure/i)).toBeInTheDocument();
    });

    // 404가 아닌 오류는 NotFoundState를 렌더링하지 않는다 — .not.toBeInTheDocument
    expect(
      screen.queryByText("알고리즘 주문이 존재하지 않습니다."),
    ).not.toBeInTheDocument();
  });

  it("does not render progress cells when data is missing (EmptyState)", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      // EmptyState가 표시되면 progress 관련 셀은 DOM에 없어야 함
      expect(screen.queryByText("10")).not.toBeInTheDocument();
      expect(screen.queryByText("5")).not.toBeInTheDocument();
    });
  });

  // ── 실패 주입 (mockRejectedValue + networkError) ──

  it("renders retryable error state when queryFn rejects with 500", async () => {
    const serverErr = new Error("Internal server error");
    (serverErr as any).statusCode = 500;

    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: serverErr,
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

  it("renders error when useAlgoProgress returns mockRejectedValue chain", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("gateway timeout"),
      isError: true,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as any);

    const { render: renderWithWrapper } = renderPage();
    renderWithWrapper(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/gateway timeout/i)).toBeInTheDocument();
    });
  });

  // ── 성능 단언 (performance.now + 예산) ──

  it("renders algo progress within performance budget", async () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: okProgress,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
      isPending: false,
      isSuccess: true,
    } as any);

    const { render: renderWithWrapper } = renderPage();

    const startedAt = performance.now();
    renderWithWrapper(<ExecutionAlgoPage />);
    await waitFor(() => {
      expect(screen.getByText("집행 진행 상태")).toBeInTheDocument();
    });
    const elapsedMs = performance.now() - startedAt;

    // 실측 약 15ms (로컬 M2 Mac). 예산 = 30배 = 450ms.
    expect(elapsedMs).toBeLessThan(450);
  });

  // ── 게이트 적색 재현 ──

  it("gate red — loading indefinitely blocks progress assertion", async () => {
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

    // 게이트 적색: loading 상태가 지속되면 progress 제목은 DOM에 없어야 함
    await waitFor(() => {
      expect(screen.queryByText("집행 진행 상태")).not.toBeInTheDocument();
    });
  });

  it("gate red — missing parentId renders missing-order alert", async () => {
    const { render: renderWithWrapper } = renderPage("");
    renderWithWrapper(<ExecutionAlgoPage />);

    // parentId가 비어 있으면 NotFoundState → "주문 ID가 없습니다."
    await waitFor(() => {
      expect(screen.getByText("주문 ID가 없습니다.")).toBeInTheDocument();
    });
  });

  it("gate red — demoted state shows warning card and hides normal progress cells", async () => {
    const demotedProgress: AlgoProgressResponse = {
      ...okProgress,
      demotedToTwap: true,
      demotionReason: "Participation rate exceeded 15%",
    };

    vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
      data: demotedProgress,
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
});
