import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { PortfolioPage } from "./PortfolioPage";
import { PortfolioPositionsSection } from "./PortfolioPositionsSection";

let allocations: unknown[] = [];
const mutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  usePortfolio: () => ({
    data: {
      allocations,
      unallocatedCash: "1000.00",
      unallocatedCashWeightPct: "100",
      totalPortfolioValue: "1000.00",
    },
    isLoading: false,
    dataUpdatedAt: Date.parse("2026-09-03T00:00:00Z"),
  }),
  useRebalancePortfolio: () => ({ mutateAsync, isPending: false, data: undefined }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  allocations = [];
  mutateAsync.mockReset();
});

function renderPage() {
  render(
    <MemoryRouter>
      <PortfolioPage />
    </MemoryRouter>,
  );
}

const SNAPSHOT = {
  position_key: "upbit:BTC-KRW:strat-1:exec-1",
  tenant_id: "t-1",
  account_id: "a-1",
  instrument_id: "i-1",
  quantity: "1.5",
  avg_cost: { amount: "50000.00", currency: "KRW" },
  cost_method: "FIFO",
  lots: [],
  realized_pnl_base: "1000.00",
  unrealized_pnl_base: "2500.00",
  fees_base: "10.00",
  funding_base: "0.00",
  mark_price: { amount: "51666.67", currency: "KRW" },
  mark_at: "2026-09-03T00:00:00Z",
  base_currency: "KRW",
  last_journal_seq: 3,
  updated_at: "2026-09-03T00:00:00Z",
  schema_version: "v1",
};

// task-709 §3.2 (B): PortfolioPage는 자체 매핑 대신 positionView.ts 파서를 쓰는
// PortfolioPositionsSection을 배선한다. 서버 라우트가 아직 없어(task-628
// decision) 화면 자체는 빈 배열로 렌더링하고, 파싱·표시 로직은 이 섹션을 직접
// 렌더링해 검증한다(PositionPnLCard.test.tsx와 같은 패턴).
describe("PortfolioPage", () => {
  it("포지션 스냅샷 섹션을 배선해 빈 상태를 보여준다", () => {
    renderPage();

    expect(screen.getByTestId("portfolio-positions-section")).toBeInTheDocument();
    expect(screen.getByText("포지션 스냅샷이 없습니다.")).toBeInTheDocument();
  });
});

describe("PortfolioPositionsSection", () => {
  it("정상 스냅샷은 position_key와 미실현 손익을 표시한다", () => {
    render(<PortfolioPositionsSection positions={[SNAPSHOT]} />);

    expect(screen.getByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(screen.getByText("2500.00")).toBeInTheDocument();
    expect(screen.queryByText("평가 불가")).not.toBeInTheDocument();
  });

  it("mark price가 없으면 미실현 손익을 0으로 채우지 않고 '평가 불가'로 표기한다", () => {
    const noMark = { ...SNAPSHOT, mark_price: null, mark_at: null, unrealized_pnl_base: null };
    render(<PortfolioPositionsSection positions={[noMark]} />);

    expect(screen.getByText("평가 불가")).toBeInTheDocument();
    expect(screen.queryByText("2500.00")).not.toBeInTheDocument();
    expect(screen.getByTestId("mark-stale-badge")).toBeInTheDocument();
  });

  it("as_of가 스테일이면 지연 배너를 보여준다", () => {
    const now = new Date("2026-09-03T00:20:00Z");
    render(
      <PortfolioPositionsSection
        positions={[SNAPSHOT]}
        asOf="2026-09-03T00:00:00Z"
        now={now}
      />,
    );

    expect(screen.getByTestId("positions-stale-banner")).toBeInTheDocument();
  });

  it("as_of가 신선하면 지연 배너를 보여주지 않는다", () => {
    const now = new Date("2026-09-03T00:01:00Z");
    render(
      <PortfolioPositionsSection
        positions={[SNAPSHOT]}
        asOf="2026-09-03T00:00:00Z"
        now={now}
      />,
    );

    expect(screen.queryByTestId("positions-stale-banner")).not.toBeInTheDocument();
  });

  it("negative: schema_version이 잘못된 스냅샷은 예외 없이 danger 안내로 렌더링한다", () => {
    const badSchema = { ...SNAPSHOT, schema_version: "v2" };
    expect(() => render(<PortfolioPositionsSection positions={[badSchema]} />)).not.toThrow();
    expect(screen.getByText(/지원하지 않는 schema_version/)).toBeInTheDocument();
  });

  it("negative: NAV/PnL 파싱 실패도 예외 없이 danger 안내로 렌더링한다", () => {
    render(<PortfolioPositionsSection positions={[]} nav={{ schema_version: "v1" }} pnl={{ schema_version: "v2" }} />);

    expect(screen.getByTestId("nav-snapshot-error")).toBeInTheDocument();
    expect(screen.getByTestId("pnl-breakdown-error")).toBeInTheDocument();
  });
});

function allocation(overrides: Record<string, unknown> = {}) {
  return {
    executionId: 1,
    strategyId: "strat-1",
    weightPct: "50",
    totalPnl: "10.00",
    allocatedCapital: "500.00",
    ...overrides,
  };
}

// task-901 §3.3: 재조정 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("PortfolioPage 재조정 에러 표시", () => {
  it("negative: VALIDATION_*(400) 실패는 err.message 대신 BadRequestNotice의 매핑 문구를 보여준다", async () => {
    allocations = [allocation()];
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw server detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("500.00"), { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: "재조정 적용" }));

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    allocations = [allocation()];
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("500.00"), { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: "재조정 적용" }));

    await waitFor(() => expect(screen.getByText("재조정에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
