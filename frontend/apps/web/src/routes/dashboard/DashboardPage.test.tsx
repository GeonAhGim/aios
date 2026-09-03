import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";

let portfolioResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false };
let executionsResult: { data: unknown; isLoading: boolean } = { data: [], isLoading: false };

vi.mock("@aios/shared-hooks", () => ({
  useRiskProfile: () => ({ data: undefined }),
  usePortfolio: () => portfolioResult,
  useExecutions: () => executionsResult,
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
