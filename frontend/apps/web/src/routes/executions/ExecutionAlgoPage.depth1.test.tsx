import "../../i18n";
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { bestOfMsAsync } from "../../test/perfBudget";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ExecutionAlgoPage } from "./ExecutionAlgoPage";
import type { AlgoProgressResponse } from "@aios/shared-types";
import * as useEmsHooks from "@aios/shared-hooks";

// 예산 근거: 로컬 실측 render+waitFor 14.4~26.4ms (vitest jsdom, 3회) → 최대치의 30배 이상 여유
const PERF_BUDGET_MS = 1000;

// Route-param mock (same pattern as ExecutionAlgoPage.test.tsx)
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

function renderPage(parentId = "550e8400-e29b-41d4-a716-446655440000") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );

  mockRouteParams.parentId = parentId;
  return { render: (el: React.ReactElement) => render(el, { wrapper: Wrapper }) };
}

// 훅 반환값 주입 — 기존 테스트와 같은 `as any` 패턴
function mockHook(overrides: Record<string, unknown>) {
  vi.spyOn(useEmsHooks, "useAlgoProgress").mockReturnValue({
    data: undefined,
    isLoading: false,
    error: null,
    isError: false,
    refetch: vi.fn(),
    isPending: false,
    isSuccess: false,
    ...overrides,
  } as any);
}

// 진행 상태 카드 단언 — gate red 테스트가 같은 단언을 재사용한다
const expectProgressCard = () => {
  expect(screen.getByText("집행 진행 상태")).toBeInTheDocument();
};

// 같은 단언이 망가진 입력에서 실제로 실패하는지 확인 (짧은 timeout으로 적색 재현)
const expectAssertionFails = (assertion: () => void) =>
  expect(waitFor(assertion, { timeout: 100 })).rejects.toThrow();

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

describe("ExecutionAlgoPage — DEEPEN-DEPTH EM-18 D2 보강", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("renders progress card on successful response", async () => {
    mockHook({ data: okProgress, isSuccess: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(expectProgressCard);
    expect(screen.getByText("500.50")).toBeInTheDocument();
  });

  // ── Negative tests (toThrow, .not.toBeInTheDocument 등) ──

  it("throws when useAlgoProgress hook itself throws on mount", () => {
    vi.spyOn(useEmsHooks, "useAlgoProgress").mockImplementation(() => {
      throw new Error("hook initialization failure");
    });

    const { render: renderWithWrapper } = renderPage();
    expect(() => renderWithWrapper(<ExecutionAlgoPage />)).toThrow("hook initialization failure");
  });

  it("does not render NotFoundState for a non-404 network failure", async () => {
    const networkErr = new Error("Network failure");
    (networkErr as any).statusCode = 503;
    mockHook({ error: networkErr, isError: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/Network failure/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("알고리즘 주문이 존재하지 않습니다.")).not.toBeInTheDocument();
  });

  it("does not render progress cells when data is missing", async () => {
    mockHook({});

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.queryByText("10")).not.toBeInTheDocument();
      expect(screen.queryByText("5")).not.toBeInTheDocument();
      expect(screen.queryByText("집행 진행 상태")).not.toBeInTheDocument();
    });
  });

  it("does not show demotion warning when demotedToTwap is false", async () => {
    mockHook({ data: okProgress, isSuccess: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(expectProgressCard);
    expect(screen.queryByText("TWAP로 강등됨")).not.toBeInTheDocument();
  });

  // ── 실패 주입 (의존성 hook이 에러를 반환) ──

  it("renders error text when the query fails with 500", async () => {
    const serverErr = new Error("Internal server error");
    (serverErr as any).statusCode = 500;
    mockHook({ error: serverErr, isError: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/Internal server error/i)).toBeInTheDocument();
    });
  });

  it("renders error text when the gateway times out", async () => {
    mockHook({ error: new Error("gateway timeout"), isError: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(() => {
      expect(screen.getByText(/gateway timeout/i)).toBeInTheDocument();
    });
  });

  // ── 성능 단언 (performance.now + 예산) ──

  it("renders algo progress within performance budget", async () => {
    mockHook({ data: okProgress, isSuccess: true });

    const { render: renderWithWrapper } = renderPage();

    const elapsed = await bestOfMsAsync(async () => {
      const startedAt = performance.now();
      renderWithWrapper(<ExecutionAlgoPage />);
      await waitFor(expectProgressCard);
      const ms = performance.now() - startedAt;
      cleanup();
      return ms;
    });

    expect(elapsed).toBeLessThan(PERF_BUDGET_MS);
  });

  // ── 게이트 적색 재현: 입력/지연을 망가뜨리면 같은 단언이 실제로 실패해야 한다 ──

  it("gate red — stalled loading makes the progress assertion fail", async () => {
    mockHook({ isLoading: true, isPending: true });

    renderPage().render(<ExecutionAlgoPage />);

    await expectAssertionFails(expectProgressCard);
  });

  it("gate red — empty parentId makes the progress assertion fail", async () => {
    renderPage("").render(<ExecutionAlgoPage />);

    await expectAssertionFails(expectProgressCard);
  });

  it("gate red — non-demoted data makes the demotion assertion fail", async () => {
    mockHook({ data: okProgress, isSuccess: true });

    renderPage().render(<ExecutionAlgoPage />);

    await waitFor(expectProgressCard);
    await expectAssertionFails(() => {
      expect(screen.getByText("TWAP로 강등됨")).toBeInTheDocument();
    });
  });
});
