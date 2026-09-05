import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@aios/api-client";
import { parseNavSnapshot, parsePositionSnapshot } from "@aios/shared-types";
import type { PositionsClientLike } from "../../hooks/usePositions";
import { PortfolioPage } from "./PortfolioPage";

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
  }),
  useRebalancePortfolio: () => ({ mutateAsync, isPending: false, data: undefined }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(() => {
  cleanup();
  allocations = [];
  mutateAsync.mockReset();
});

const NOW = new Date("2026-09-05T12:03:00Z");
const AS_OF_FRESH = "2026-09-05T12:02:00Z";
const AS_OF_STALE = "2026-09-05T11:00:00Z";

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
  mark_at: "2026-09-05T12:00:00Z",
  base_currency: "KRW",
  last_journal_seq: 3,
  updated_at: "2026-09-05T12:00:00Z",
  schema_version: "v1",
};

const NAV = {
  schema_version: "v1",
  account_id: "a-1",
  nav_date: "2026-09-04",
  base_currency: "KRW",
  opening_nav: "100000.00",
  cash: "5000.00",
  positions_mv: "96000.00",
  realized: "1000.00",
  unrealized_delta: "500.00",
  funding: "0.00",
  fees: "10.00",
  flows: "0.00",
  closing_nav: "101000.00",
  fx_rates: [],
  source_hash: "abc",
};

function fakeClient(overrides: Partial<PositionsClientLike> = {}): PositionsClientLike {
  return {
    listPositions: vi.fn().mockResolvedValue({ items: [parsePositionSnapshot(SNAPSHOT)], asOf: AS_OF_FRESH }),
    getPositionJournal: vi.fn().mockResolvedValue({
      positionKey: SNAPSHOT.position_key,
      items: [],
      nextCursor: null,
      asOf: AS_OF_FRESH,
    }),
    getNavSeries: vi.fn().mockResolvedValue({
      accountId: "a-1",
      startDate: "2026-08-30",
      endDate: "2026-09-05",
      items: [parseNavSnapshot({ ...NAV, nav_date: "2026-09-02" }), parseNavSnapshot(NAV)],
      missingDates: ["2026-09-05"],
      asOf: AS_OF_FRESH,
    }),
    ...overrides,
  };
}

function renderPage(client: PositionsClientLike = fakeClient()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PortfolioPage positionsClient={client} now={NOW} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

function apiError(status: number, code: string, message = "raw server detail"): ApiError {
  return new ApiError(status, message, "trace-1", code);
}

// task-1524(LB-19): task-709가 positions=[]로 두었던 섹션을 GET /v1/positions·/nav 실데이터로
// 연결한다. 클라이언트는 주입(fake)하고, 화면이 그 결과를 파서 판별 그대로 그리는지·
// as_of(봉투 meta)로 stale을 판정하는지·에러가 기존 분류기 갈래로 가는지 고정한다.
describe("PortfolioPage 포지션 실데이터", () => {
  it("GET /v1/positions 결과를 position_key 카드로 그리고, 그 계좌로 최근 7일 NAV를 조회해 가장 늦은 NAV를 보여준다", async () => {
    const client = renderPage();

    expect(await screen.findByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(client.listPositions).toHaveBeenCalledWith({});
    await waitFor(() =>
      expect(client.getNavSeries).toHaveBeenCalledWith({
        accountId: "a-1",
        startDate: "2026-08-30",
        endDate: "2026-09-05",
      }),
    );
    expect(await screen.findByTestId("nav-snapshot-card")).toHaveTextContent("NAV · 2026-09-04");
    // 빠진 날은 0으로 채우지 않고 미산출 사실을 드러낸다.
    expect(screen.getByTestId("nav-missing-notice")).toHaveTextContent("NAV 미산출 1일");
    expect(screen.queryByTestId("positions-stale-banner")).not.toBeInTheDocument();
  });

  it("포지션이 없으면 빈 상태를 보여주고 NAV는 조회하지 않는다(account_id가 없음)", async () => {
    const client = renderPage(
      fakeClient({ listPositions: vi.fn().mockResolvedValue({ items: [], asOf: AS_OF_FRESH }) }),
    );

    expect(await screen.findByText("포지션 스냅샷이 없습니다.")).toBeInTheDocument();
    expect(client.getNavSeries).not.toHaveBeenCalled();
    expect(screen.queryByTestId("nav-snapshot-card")).not.toBeInTheDocument();
  });

  it("봉투 meta.as_of가 300초보다 오래되면 지연 배너를 보여준다(dataUpdatedAt 대체 금지)", async () => {
    renderPage(
      fakeClient({
        listPositions: vi.fn().mockResolvedValue({ items: [parsePositionSnapshot(SNAPSHOT)], asOf: AS_OF_STALE }),
      }),
    );

    expect(await screen.findByTestId("positions-stale-banner")).toBeInTheDocument();
  });

  it("mark 없음(POS_MARK_STALE)은 미실현 손익을 0이 아니라 '평가 불가'로 표기한다", async () => {
    const noMark = { ...SNAPSHOT, mark_price: null, mark_at: null, unrealized_pnl_base: null };
    renderPage(
      fakeClient({
        listPositions: vi.fn().mockResolvedValue({ items: [parsePositionSnapshot(noMark)], asOf: AS_OF_FRESH }),
      }),
    );

    expect(await screen.findByText("평가 불가")).toBeInTheDocument();
    expect(screen.getByTestId("mark-stale-badge")).toBeInTheDocument();
  });

  it("negative: 404 RESOURCE_NOT_FOUND는 NotFoundState(재시도 없음)로 그리고 서버 message를 노출하지 않는다", async () => {
    renderPage(
      fakeClient({ listPositions: vi.fn().mockRejectedValue(apiError(404, "RESOURCE_NOT_FOUND")) }),
    );

    expect(await screen.findByText("포지션을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 403 AUTH_TENANT_MISMATCH는 ForbiddenNotice 문구로 그린다", async () => {
    renderPage(
      fakeClient({ listPositions: vi.fn().mockRejectedValue(apiError(403, "AUTH_TENANT_MISMATCH")) }),
    );

    expect(await screen.findByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 503 EXCHANGE_UNAVAILABLE은 ErrorMessage + 재시도 버튼으로 그리고, 재시도가 refetch를 부른다", async () => {
    const listPositions = vi
      .fn()
      .mockRejectedValueOnce(apiError(503, "EXCHANGE_UNAVAILABLE"))
      .mockResolvedValue({ items: [parsePositionSnapshot(SNAPSHOT)], asOf: AS_OF_FRESH });
    renderPage(fakeClient({ listPositions }));

    expect(
      await screen.findByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect(await screen.findByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(listPositions).toHaveBeenCalledTimes(2);
  });

  it("negative: NAV 조회 실패는 포지션 카드를 막지 않고 별도 오류 영역에만 그린다", async () => {
    renderPage(
      fakeClient({ getNavSeries: vi.fn().mockRejectedValue(apiError(404, "RESOURCE_NOT_FOUND")) }),
    );

    expect(await screen.findByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(await screen.findByText("NAV를 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByTestId("nav-snapshot-card")).not.toBeInTheDocument();
  });

  // task-936: GET /portfolio는 아직 봉투 미적용이라 헤더 신선도는 "확인 불가"로 남는다 —
  // 포지션 섹션의 as_of 배선과 무관하게 헤더 stale 배지가 생기지 않는지 유지한다.
  it("negative: 포트폴리오 헤더는 meta.as_of가 없어 확인 불가를 보여주고 stale 배지를 그리지 않는다", async () => {
    renderPage();

    expect(screen.getByText("기준 시각 확인 불가")).toBeInTheDocument();
    await screen.findByText("upbit:BTC-KRW:strat-1:exec-1");
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
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

  // task-1215 회귀: VALIDATION_INVALID_FIELD(400) + details.fields=[]는 서버 message
  // 배너로 폴백한다(task-1214) — 재조정 실패(금전 화면)가 무응답으로 사라지지 않게 잠근다.
  it("negative: VALIDATION_INVALID_FIELD(400) + 빈 details.fields는 서버 message 배너로 폴백한다(P0 무응답 회귀 방지)", async () => {
    allocations = [allocation()];
    mutateAsync.mockRejectedValue(
      new ApiError(
        400,
        "재조정 후 배분 비중 합이 100%를 초과합니다.",
        undefined,
        "VALIDATION_INVALID_FIELD",
        undefined,
        { fields: [] },
      ),
    );
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("500.00"), { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: "재조정 적용" }));

    await waitFor(() =>
      expect(screen.getByText("재조정 후 배분 비중 합이 100%를 초과합니다.")).toBeInTheDocument(),
    );
  });
});
