import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@aios/api-client";
import { parseNavSnapshot, parsePositionSnapshot } from "@aios/shared-types";
import type { PositionsClientLike } from "../../hooks/usePositions";
import { PortfolioPositionsLive } from "./PortfolioPositionsLive";

afterEach(() => cleanup());

const NOW = new Date("2026-09-05T12:03:00Z");
const AS_OF_FRESH = "2026-09-05T12:02:00Z";

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

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
    listPositions: vi.fn().mockResolvedValue({ items: [parsePositionSnapshot(snapshot())], asOf: AS_OF_FRESH }),
    getPositionJournal: vi.fn().mockResolvedValue({ positionKey: "x", items: [], nextCursor: null, asOf: AS_OF_FRESH }),
    getNavSeries: vi.fn().mockResolvedValue({
      accountId: "a-1",
      startDate: "2026-08-30",
      endDate: "2026-09-05",
      items: [parseNavSnapshot(NAV)],
      missingDates: [],
      asOf: AS_OF_FRESH,
    }),
    ...overrides,
  };
}

function renderLive(client: PositionsClientLike = fakeClient()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PortfolioPositionsLive client={client} now={NOW} />
    </QueryClientProvider>,
  );
  return client;
}

function apiError(status: number, code: string, message = "raw server detail"): ApiError {
  return new ApiError(status, message, "trace-1", code);
}

describe("PortfolioPositionsLive 포지션·NAV 조회", () => {
  it("포지션 목록과 그 계좌의 NAV를 조회해 카드로 그린다", async () => {
    const client = renderLive();

    expect(await screen.findByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    await waitFor(() =>
      expect(client.getNavSeries).toHaveBeenCalledWith({
        accountId: "a-1",
        startDate: "2026-08-30",
        endDate: "2026-09-05",
      }),
    );
    expect(await screen.findByTestId("nav-snapshot-card")).toBeInTheDocument();
  });

  it("포지션이 없으면 계좌를 알 수 없어 NAV를 조회하지 않는다", async () => {
    const client = renderLive(fakeClient({ listPositions: vi.fn().mockResolvedValue({ items: [], asOf: AS_OF_FRESH }) }));

    expect(await screen.findByText("포지션 스냅샷이 없습니다.")).toBeInTheDocument();
    expect(client.getNavSeries).not.toHaveBeenCalled();
  });

  it("NAV missingDates가 있으면 미산출 안내를 보여준다", async () => {
    renderLive(
      fakeClient({
        getNavSeries: vi.fn().mockResolvedValue({
          accountId: "a-1",
          startDate: "2026-08-30",
          endDate: "2026-09-05",
          items: [parseNavSnapshot(NAV)],
          missingDates: ["2026-09-05"],
          asOf: AS_OF_FRESH,
        }),
      }),
    );

    expect(await screen.findByTestId("nav-missing-notice")).toHaveTextContent("NAV 미산출 1일");
  });
});

describe("PortfolioPositionsLive 다계좌 NAV 선택", () => {
  it("계좌가 둘 이상이면 NAV 계좌 선택 드롭다운을 보여주고 첫 계좌를 기본 선택한다", async () => {
    const client = renderLive(
      fakeClient({
        listPositions: vi.fn().mockResolvedValue({
          items: [parsePositionSnapshot(snapshot()), parsePositionSnapshot(snapshot({ position_key: "upbit:ETH-KRW:strat-1:exec-1", account_id: "a-2" }))],
          asOf: AS_OF_FRESH,
        }),
      }),
    );

    expect(await screen.findByTestId("nav-account-select")).toBeInTheDocument();
    await waitFor(() =>
      expect(client.getNavSeries).toHaveBeenCalledWith({ accountId: "a-1", startDate: "2026-08-30", endDate: "2026-09-05" }),
    );
  });

  it("NAV 계좌를 바꾸면 선택한 계좌로 다시 조회한다", async () => {
    const client = renderLive(
      fakeClient({
        listPositions: vi.fn().mockResolvedValue({
          items: [parsePositionSnapshot(snapshot()), parsePositionSnapshot(snapshot({ position_key: "upbit:ETH-KRW:strat-1:exec-1", account_id: "a-2" }))],
          asOf: AS_OF_FRESH,
        }),
      }),
    );
    await screen.findByTestId("nav-account-select");

    fireEvent.change(screen.getByTestId("nav-account-select"), { target: { value: "a-2" } });

    await waitFor(() =>
      expect(client.getNavSeries).toHaveBeenCalledWith({ accountId: "a-2", startDate: "2026-08-30", endDate: "2026-09-05" }),
    );
  });
});

describe("PortfolioPositionsLive 에러 표시", () => {
  it("negative: positions 조회 실패는 PositionsQueryError로 그린다", async () => {
    renderLive(fakeClient({ listPositions: vi.fn().mockRejectedValue(apiError(404, "RESOURCE_NOT_FOUND")) }));

    expect(await screen.findByTestId("portfolio-positions-error")).toBeInTheDocument();
    expect(await screen.findByText("포지션을 찾을 수 없습니다.")).toBeInTheDocument();
  });

  it("negative: NAV 조회 실패는 포지션 카드를 막지 않고 별도 오류 영역에만 그린다", async () => {
    renderLive(fakeClient({ getNavSeries: vi.fn().mockRejectedValue(apiError(404, "RESOURCE_NOT_FOUND")) }));

    expect(await screen.findByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(await screen.findByTestId("nav-series-error")).toBeInTheDocument();
    expect(await screen.findByText("NAV를 찾을 수 없습니다.")).toBeInTheDocument();
  });
});
