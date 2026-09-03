import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ParsedPnLBreakdown, ParsedPositionSnapshot, PositionSnapshotView } from "@aios/shared-types";
import { PositionPnLCard } from "./PositionPnLCard";

afterEach(cleanup);

const SNAPSHOT: PositionSnapshotView = {
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
};

const OK: ParsedPositionSnapshot = { kind: "ok", value: SNAPSHOT };

describe("PositionPnLCard", () => {
  it("정상 스냅샷은 position_key·수량·평균단가·미실현 손익을 표시한다", () => {
    render(<PositionPnLCard snapshot={OK} />);

    expect(screen.getByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(screen.getByText("1.5")).toBeInTheDocument();
    expect(screen.getByText("50000.00 KRW")).toBeInTheDocument();
    expect(screen.getByText("2500.00")).toBeInTheDocument();
    expect(screen.queryByTestId("mark-stale-badge")).not.toBeInTheDocument();
  });

  it("mark_price/mark_at이 null이면 스테일 배지를 보여준다", () => {
    const stale: ParsedPositionSnapshot = {
      kind: "ok",
      value: { ...SNAPSHOT, mark_price: null, mark_at: null, unrealized_pnl_base: null },
    };
    render(<PositionPnLCard snapshot={stale} />);

    expect(screen.getByTestId("mark-stale-badge")).toBeInTheDocument();
  });

  it("unrealized_pnl_base가 null이면 0이 아니라 '평가 불가'로 구분 표기한다", () => {
    const noMark: ParsedPositionSnapshot = {
      kind: "ok",
      value: { ...SNAPSHOT, unrealized_pnl_base: null },
    };
    render(<PositionPnLCard snapshot={noMark} />);

    expect(screen.getByText("평가 불가")).toBeInTheDocument();
    expect(screen.queryByText("2500.00")).not.toBeInTheDocument();
  });

  it("PnLBreakdown이 함께 주어지면 합산 행을 표시한다", () => {
    const pnl: ParsedPnLBreakdown = {
      kind: "ok",
      value: {
        realized: "1000.00",
        unrealized: "2500.00",
        fees: "10.00",
        funding: "0.00",
        total: "3490.00",
        base_currency: "KRW",
        fx_rates_used: [],
      },
    };
    render(<PositionPnLCard snapshot={OK} pnl={pnl} />);

    expect(screen.getByTestId("pnl-breakdown")).toBeInTheDocument();
    expect(screen.getByText("3490.00 KRW")).toBeInTheDocument();
  });

  it("negative: schema_version 불일치는 예외 없이 danger 안내로 렌더링한다", () => {
    const unsupported: ParsedPositionSnapshot = { kind: "unsupported_schema_version", received: "v2" };
    expect(() => render(<PositionPnLCard snapshot={unsupported} />)).not.toThrow();
    expect(screen.getByText(/지원하지 않는 schema_version/)).toBeInTheDocument();
  });

  it("negative: invalid 파싱 결과는 예외 없이 danger 안내로 렌더링한다", () => {
    const invalid: ParsedPositionSnapshot = { kind: "invalid" };
    expect(() => render(<PositionPnLCard snapshot={invalid} />)).not.toThrow();
    expect(screen.getByText("포지션 데이터를 해석할 수 없습니다.")).toBeInTheDocument();
  });
});
