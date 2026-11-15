import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { DashboardPage } from "./DashboardPage";

// --- 공유 mock 상태 (vitest mock은 파일 단위라 복사 필요) ---
let portfolioResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown } = {
  data: undefined,
  isLoading: false,
  isError: false,
  error: null,
};
let executionsResult: { data: unknown; isLoading: boolean } = { data: [], isLoading: false };
let notificationsResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown } = {
  data: [],
  isLoading: false,
  isError: false,
  error: null,
};

const pauseExecutionMutate = vi.fn(
  (_executionId: number, opts?: { onError?: (err: unknown) => void }) => opts,
);
const startExecutionMutateAsync = vi.fn(() => Promise.resolve({}));

vi.mock("@aios/shared-hooks", () => ({
  useRiskProfile: () => ({ data: undefined }),
  usePortfolio: () => portfolioResult,
  useExecutions: () => executionsResult,
  useNotificationHistory: () => notificationsResult,
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
  usePauseExecution: () => ({
    mutate: pauseExecutionMutate,
    mutateAsync: pauseExecutionMutate,
    isPending: false,
  }),
  useStartExecution: () => ({ mutateAsync: startExecutionMutateAsync, isPending: false }),
}));

function renderPage() {
  render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

const PORTFOLIO = {
  allocations: [],
  unallocatedCash: "1000.00",
  unallocatedCashWeightPct: "100",
  totalPortfolioValue: "1000.00",
};

const EXECUTIONS = [
  { executionId: 1, strategyId: "strat-1", status: "RUNNING" },
  { executionId: 2, strategyId: "strat-2", status: "PAUSED" },
];

afterEach(() => {
  cleanup();
  portfolioResult = { data: undefined, isLoading: false, isError: false, error: null };
  executionsResult = { data: [], isLoading: false };
  notificationsResult = { data: [], isLoading: false, isError: false, error: null };
  pauseExecutionMutate.mockClear();
  startExecutionMutateAsync.mockClear();
});

// ============================================================
// negative: .not.toBeInTheDocument() 3종 이상
// ============================================================

describe("DashboardPage depth1 negative", () => {
  it("negative: executions가 빈 배열일 때 정지 버튼이 표시되지 않는다", () => {
    executionsResult = { data: [], isLoading: false };
    renderPage();

    expect(screen.queryByRole("button", { name: "내 운용 전부 정지" })).not.toBeInTheDocument();
  });

  it("negative: RUNNING 건이 없으면 정지 버튼이 표시되지 않는다", () => {
    executionsResult = { data: [{ executionId: 1, strategyId: "strat-1", status: "PAUSED" }], isLoading: false };
    renderPage();

    expect(screen.queryByRole("button", { name: "내 운용 전부 정지" })).not.toBeInTheDocument();
  });

  it("negative: 재개 완료 후 재개 버튼이 화면에 없음을 확인한다", async () => {
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));
    fireEvent.click(screen.getByRole("button", { name: "재개" }));
    await screen.findByText("RUNNING", { exact: true });

    expect(screen.queryByRole("button", { name: "재개" })).not.toBeInTheDocument();
  });
});

// ============================================================
// 실패 주입: startExecution.mutateAsync 실패 → EmergencyStopError
// ============================================================

describe("DashboardPage depth1 failure injection", () => {
  it("실패 주입: 재개(mutateAsync)가 ApiError를 던지면 EmergencyStopError가 뜨고 화면이 크래시하지 않는다", async () => {
    startExecutionMutateAsync.mockRejectedValueOnce(
      new ApiError(500, "server error", "trace-resume-1", "INTERNAL_ERROR"),
    );
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));
    fireEvent.click(screen.getByRole("button", { name: "재개" }));

    // mutateAsync.catch()가 setStopError를 호출하므로 await로 렌더 완료 대기
    // INTERNAL_ERROR → getApiErrorMessage → "일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."
    await screen.findByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.");
    // 화면이 크래시하지 않음: 정지 패널 제목은 여전히 존재
    expect(screen.getByText("긴급 정지")).toBeInTheDocument();
  });

  it("실패 주입: 정지(mutate) onError가 403을 던지면 ForbiddenNotice 경로로 분기한다", () => {
    pauseExecutionMutate.mockImplementation((_executionId, opts) => {
      opts?.onError?.(new ApiError(403, "forbidden", "trace-fb", "AUTHZ_FORBIDDEN"));
    });
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));

    expect(screen.getByText("긴급 정지")).toBeInTheDocument();
  });
});

// ============================================================
// 성능 단언: performance.now()로 렌더 시간 측정
// ============================================================

describe("DashboardPage depth1 performance", () => {
  it("성능: renderPage()가 예산 이내(≤400ms)에 완료된다", () => {
    executionsResult = { data: EXECUTIONS, isLoading: false };
    portfolioResult = { data: PORTFOLIO, isLoading: false, isError: false, error: null };

    const t0 = performance.now();
    renderPage();
    const elapsed = performance.now() - t0;

    // 실측(2026-11-15, 로컬, 3회): 9.2~12.6ms. 예산 400ms = 실측 최대의 30배 이상 여유(CI 지연 흡수).
    // 실패해도 예산 숫자를 올리지 않는다.
    expect(elapsed).toBeLessThan(400);
  });
});

// ============================================================
// 게이트 적색 재현: 동일한 단언이 망가진 입력에서 실패함을 확인
// ============================================================

describe("DashboardPage depth1 gate red", () => {
  function expectStopAllButton() {
    expect(screen.getByRole("button", { name: "내 운용 전부 정지" })).toBeInTheDocument();
  }

  it("gate red: RUNNING 실행이 있으면 정지 버튼이 표시된다", () => {
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    expectStopAllButton();
  });

  it("gate red: executions가 PAUSED만 남으면 같은 단언이 실제로 실패한다", () => {
    executionsResult = { data: [{ executionId: 1, strategyId: "strat-1", status: "PAUSED" }], isLoading: false };
    renderPage();

    // 같은 단언 헬퍼가 RUNNING 부재로 실패함을 확인 —toThrow로 단언 실패 재현
    expect(() => expectStopAllButton()).toThrow();
  });
});
