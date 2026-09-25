import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ExchangePositionsCard } from "./ExchangePositionsCard";

let queryResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: ReturnType<typeof vi.fn>;
};

vi.mock("@aios/shared-hooks", () => ({
  useExchangePositions: () => queryResult,
}));

function resetQuery(overrides: Partial<typeof queryResult> = {}) {
  queryResult = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

function position(overrides: Record<string, unknown> = {}) {
  return {
    symbol: "BTCUSDT",
    exchange: "bitget",
    strategyId: "s-1",
    executionId: null,
    quantity: "0.5",
    averageEntryPrice: { amount: "60000", currency: "USDT" },
    currentPrice: { amount: "61000", currency: "USDT" },
    unrealizedPnl: { amount: "500", currency: "USDT" },
    realizedPnl: { amount: "0", currency: "USDT" },
    leverage: "1",
    margin: null,
    entryTime: "2026-09-17T00:00:00+00:00",
    updatedAt: "2026-09-17T00:00:00+00:00",
    assetClass: "CRYPTO",
    optionType: null,
    strikePrice: null,
    expiryDate: null,
    contractMultiplier: null,
    underlyingSymbol: null,
    ...overrides,
  };
}

// task-4003(FE-OPS-10c) DoD(b): 자격증명 없는 거래소(404) → routeApiError 문구,
// 5xx → 재시도 안내, []→ 빈 상태 렌더 — 세 케이스를 DOM 텍스트로 단언한다.
describe("ExchangePositionsCard", () => {
  it("negative: exchange가 선택되지 않으면 아직 조회하지 않고 안내만 보여준다", () => {
    resetQuery();
    render(<ExchangePositionsCard exchange={null} />);

    expect(
      screen.getByText("거래소를 선택하면 보유 포지션을 볼 수 있습니다."),
    ).toBeInTheDocument();
  });

  it("negative: 자격증명 없는 거래소(404 RESOURCE_NOT_FOUND)는 '없음' 문구를 보여주고 재시도 버튼이 없다", () => {
    resetQuery({
      isError: true,
      error: new ApiError(404, "자격증명이 없습니다.", "t-1", "RESOURCE_NOT_FOUND"),
    });
    render(<ExchangePositionsCard exchange="bitget" />);

    expect(screen.getByText("이 거래소의 자격증명이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("negative: 5xx(EXCHANGE_UNAVAILABLE)는 재시도 안내와 버튼을 보여주고 클릭 시 refetch를 호출한다", () => {
    const refetch = vi.fn();
    resetQuery({
      isError: true,
      error: new ApiError(503, "raw", "t-2", "EXCHANGE_UNAVAILABLE"),
      refetch,
    });
    render(<ExchangePositionsCard exchange="bitget" />);

    expect(
      screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("빈 배열([])이면 빈 상태를 렌더한다", () => {
    resetQuery({ data: [] });
    render(<ExchangePositionsCard exchange="bitget" />);

    expect(screen.getByText("Bitget에 보유 포지션이 없습니다.")).toBeInTheDocument();
  });

  it("포지션이 있으면 심볼·수량·평단·미실현손익을 표로 보여준다", () => {
    resetQuery({ data: [position()] });
    render(<ExchangePositionsCard exchange="bitget" />);

    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("0.5")).toBeInTheDocument();
    expect(screen.getByText("60000 USDT")).toBeInTheDocument();
    expect(screen.getByText("500 USDT")).toBeInTheDocument();
  });

  it("negative: 조회 중에는 로딩 상태를 보여주고 표를 렌더하지 않는다", () => {
    resetQuery({ isLoading: true });
    render(<ExchangePositionsCard exchange="bitget" />);

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
