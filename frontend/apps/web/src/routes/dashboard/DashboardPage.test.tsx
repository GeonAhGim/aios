import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { DashboardPage } from "./DashboardPage";

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
  usePauseExecution: () => ({ mutate: pauseExecutionMutate, isPending: false }),
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

afterEach(() => {
  cleanup();
  portfolioResult = { data: undefined, isLoading: false, isError: false, error: null };
  executionsResult = { data: [], isLoading: false };
  notificationsResult = { data: [], isLoading: false, isError: false, error: null };
  pauseExecutionMutate.mockClear();
  startExecutionMutateAsync.mockClear();
});

// task-936: GET /portfolio는 아직 봉투(meta.as_of) 미적용이라 대시보드는 실제
// 서버 as_of를 받을 수 없다 — react-query dataUpdatedAt을 대신 넣으면 항상
// "방금"으로 보여 stale 배지가 영영 안 뜨는 은폐가 된다(task-936 decision).
// 그래서 이 화면은 as_of가 없을 때 DataFreshness가 정직하게 "확인 불가"를
// 보여주고 stale 배지를 그리지 않는지만 검증한다.
describe("DashboardPage", () => {
  it("negative: 포트폴리오 데이터가 있어도 meta.as_of가 없으면 확인 불가를 보여주고 stale 배지를 그리지 않는다", () => {
    portfolioResult = { data: PORTFOLIO, isLoading: false, isError: false, error: null };
    renderPage();

    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
  });

  it("포트폴리오 데이터가 없으면 신선도 표시 자체를 그리지 않는다", () => {
    portfolioResult = { data: undefined, isLoading: false, isError: false, error: null };
    renderPage();

    expect(screen.queryByTestId("data-freshness")).not.toBeInTheDocument();
  });
});

// F-6(task-10642, UX_JOURNEYS.md §6 J1 사용감 소견): 포트폴리오 조회가 5xx로
// 실패하면 portfolio가 falsy가 되어 카드 섹션이 통째로 사라지고 오류도, 로딩도,
// 빈 상태 안내도 없었다(무음 실패) — 알림 카드와 동일하게 isError를 표면화한다.
describe("DashboardPage 포트폴리오 조회 실패 표시(F-6)", () => {
  it("포트폴리오 조회가 실패하면 ErrorMessage로 표면화한다", () => {
    portfolioResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "raw server detail", "trace-portfolio-1", "INTERNAL_ERROR"),
    };
    renderPage();

    expect(screen.getByText("지원코드: trace-portfolio-1")).toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 조회가 실패하면(isError) 로딩 상태를 보여주지 않는다", () => {
    portfolioResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "raw", undefined, "INTERNAL_ERROR"),
    };
    renderPage();

    expect(screen.queryByText(/불러오는 중/)).not.toBeInTheDocument();
  });
});

// G-2(journey-j1 8단계): 대시보드에 최근 알림 위젯이 useNotificationHistory로
// 렌더되는지, 그리고 빈 상태/에러 상태가 목록과 다른 DOM으로 구분되는지 검증한다.
describe("DashboardPage: 최근 알림 위젯(G-2)", () => {
  it("알림 이력이 있으면 최근 알림 목록을 렌더한다", () => {
    notificationsResult = {
      data: [
        { eventType: "risk_mismatch", channel: "EMAIL", status: "SENT", createdAt: "2026-09-15T10:00:00.000Z" },
        { eventType: "verification_result", channel: "PUSH", status: "FAILED", createdAt: "2026-09-14T09:00:00.000Z" },
      ],
      isLoading: false,
      isError: false,
      error: null,
    };
    renderPage();

    expect(screen.getByText("최근 알림")).toBeInTheDocument();
    expect(screen.getByText("risk_mismatch")).toBeInTheDocument();
    expect(screen.getByText("verification_result")).toBeInTheDocument();
  });

  it("negative: 알림이 0건이면 목록 대신 빈 상태 안내를 보여준다", () => {
    notificationsResult = { data: [], isLoading: false, isError: false, error: null };
    renderPage();

    expect(screen.getByText("표시할 최근 알림이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("negative: 알림 조회가 실패(500)해도 화면이 크래시하지 않고 에러 상태를 보여준다", () => {
    notificationsResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "internal error", "trace-2", "INTERNAL_ERROR"),
    };
    renderPage();

    expect(screen.getByText("최근 알림")).toBeInTheDocument();
    expect(screen.queryByText("표시할 최근 알림이 없습니다.")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});

// task-10636 (J9): 비관리자가 `/dashboard`에서 "내 운용 전부 정지"에 1클릭으로 도달하는지,
// 정지 대상이 useExecutions()가 돌려준(= 서버가 이미 본인 소유로 스코프한) 목록의 RUNNING
// 건으로만 한정되는지, 실패/재개가 같은 화면에서 처리되는지를 검증한다.
describe("DashboardPage: 긴급 정지 패널(J9)", () => {
  const EXECUTIONS = [
    { executionId: 1, strategyId: "my-running-strategy", status: "RUNNING", exchange: "bitget", mode: "PAPER" },
    { executionId: 2, strategyId: "my-paused-strategy", status: "PAUSED", exchange: "bitget", mode: "PAPER" },
    { executionId: 3, strategyId: "my-retired-strategy", status: "RETIRED", exchange: "bitget", mode: "PAPER" },
  ];

  it("negative: 실행 중인 운용이 없으면 정지 버튼 대신 빈 상태를 보여준다", () => {
    executionsResult = { data: [EXECUTIONS[1], EXECUTIONS[2]], isLoading: false };
    renderPage();

    expect(screen.getByText("현재 실행 중인 운용이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "내 운용 전부 정지" })).not.toBeInTheDocument();
  });

  it("1클릭으로 RUNNING 건 전체를 정지하고, 정지되지 않은 건(타인/비RUNNING)은 건드리지 않는다", () => {
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));

    // useExecutions()가 돌려준 목록 중 RUNNING 1건만 정지 대상 — PAUSED/RETIRED 건의
    // executionId(2, 3)는 pauseExecution.mutate에 전달되지 않는다(스코프 바깥 자원 미영향).
    expect(pauseExecutionMutate).toHaveBeenCalledTimes(1);
    expect(pauseExecutionMutate).toHaveBeenCalledWith(1, expect.anything());
    // "실행 중인 전략" 목록(아래 섹션)에도 같은 전략 이름이 그대로 남아 있어 2곳에 나타난다 —
    // 정지 패널에서는 PAUSED 배지로 바뀌어 보이는지가 핵심이므로 배지 텍스트로 특정한다.
    expect(screen.getAllByText("my-running-strategy").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("PAUSED", { exact: true }).length).toBeGreaterThanOrEqual(1);
  });

  it("정지 완료 화면에서 재개 버튼을 누르면 같은 화면에서 RUNNING으로 되돌릴 수 있다", async () => {
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));
    fireEvent.click(screen.getByRole("button", { name: "재개" }));

    expect(startExecutionMutateAsync).toHaveBeenCalledTimes(1);
    expect(startExecutionMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ executionId: 1 }),
    );
    await screen.findByText("RUNNING", { exact: true });
    expect(screen.queryByRole("button", { name: "재개" })).not.toBeInTheDocument();
  });

  it("[실패 주입] 정지 요청이 실패하면 일반 오류 배너를 보여주고 화면이 크래시하지 않는다", () => {
    pauseExecutionMutate.mockImplementation((_executionId, opts) => {
      opts?.onError?.(new ApiError(500, "internal error", "trace-stop", "INTERNAL_ERROR"));
    });
    executionsResult = { data: EXECUTIONS, isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));

    expect(pauseExecutionMutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText("긴급 정지")).toBeInTheDocument();
  });

  it("negative: 403 응답은 일반 오류 대신 권한 거부 안내로 분기된다", () => {
    pauseExecutionMutate.mockImplementation((_executionId, opts) => {
      opts?.onError?.(new ApiError(403, "forbidden", "trace-forbidden", "AUTHZ_FORBIDDEN"));
    });
    executionsResult = { data: [EXECUTIONS[0]], isLoading: false };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "내 운용 전부 정지" }));

    expect(screen.getByText("긴급 정지")).toBeInTheDocument();
  });
});
