import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { DashboardPage } from "./DashboardPage";

let portfolioResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false };
let executionsResult: { data: unknown; isLoading: boolean } = { data: [], isLoading: false };
let notificationsResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown } = {
  data: [],
  isLoading: false,
  isError: false,
  error: null,
};

vi.mock("@aios/shared-hooks", () => ({
  useRiskProfile: () => ({ data: undefined }),
  usePortfolio: () => portfolioResult,
  useExecutions: () => executionsResult,
  useNotificationHistory: () => notificationsResult,
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
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
  portfolioResult = { data: undefined, isLoading: false };
  executionsResult = { data: [], isLoading: false };
  notificationsResult = { data: [], isLoading: false, isError: false, error: null };
});

// task-936: GET /portfolio는 아직 봉투(meta.as_of) 미적용이라 대시보드는 실제
// 서버 as_of를 받을 수 없다 — react-query dataUpdatedAt을 대신 넣으면 항상
// "방금"으로 보여 stale 배지가 영영 안 뜨는 은폐가 된다(task-936 decision).
// 그래서 이 화면은 as_of가 없을 때 DataFreshness가 정직하게 "확인 불가"를
// 보여주고 stale 배지를 그리지 않는지만 검증한다.
describe("DashboardPage", () => {
  it("negative: 포트폴리오 데이터가 있어도 meta.as_of가 없으면 확인 불가를 보여주고 stale 배지를 그리지 않는다", () => {
    portfolioResult = { data: PORTFOLIO, isLoading: false };
    renderPage();

    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
  });

  it("포트폴리오 데이터가 없으면 신선도 표시 자체를 그리지 않는다", () => {
    portfolioResult = { data: undefined, isLoading: false };
    renderPage();

    expect(screen.queryByTestId("data-freshness")).not.toBeInTheDocument();
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
