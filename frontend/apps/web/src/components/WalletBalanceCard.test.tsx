import "@testing-library/jest-dom/vitest";
import type { WalletBalance } from "@aios/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WalletBalanceCard } from "./WalletBalanceCard";

afterEach(cleanup);

// LC-16 응답은 balance 외 available/held/pendingPayout을 얹으므로 WalletBalance
// 타입(userId/balance만 선언)보다 넓은 런타임 셰이프를 테스트에서 흉내낸다.
function fullBalance(overrides: Record<string, string>): WalletBalance {
  return {
    userId: "u-1",
    balance: "10000",
    available: "7000",
    held: "2000",
    pendingPayout: "1000",
    ...overrides,
  } as unknown as WalletBalance;
}

describe("WalletBalanceCard", () => {
  it("로딩 중에는 불러오는 중 문구를 보여준다", () => {
    render(<WalletBalanceCard balance={undefined} isLoading />);
    expect(screen.getByText("불러오는 중...")).toBeInTheDocument();
  });

  it("available/held/pendingPayout이 있으면 3분할로 표시한다", () => {
    render(<WalletBalanceCard balance={fullBalance({})} />);
    expect(screen.getByText("7,000 크레딧")).toBeInTheDocument();
    expect(screen.getByText("2,000 크레딧")).toBeInTheDocument();
    expect(screen.getByText("1,000 크레딧")).toBeInTheDocument();
  });

  it("held가 0보다 크면 주문 보류 안내를 노출한다", () => {
    render(<WalletBalanceCard balance={fullBalance({})} />);
    expect(
      screen.getByText("주문 보류 중인 금액이 있어 구매 가능 크레딧에서 제외되었습니다."),
    ).toBeInTheDocument();
  });

  it("held가 0이면 주문 보류 안내를 노출하지 않는다", () => {
    render(
      <WalletBalanceCard
        balance={fullBalance({ balance: "7000", available: "7000", held: "0", pendingPayout: "0" })}
      />,
    );
    expect(
      screen.queryByText("주문 보류 중인 금액이 있어 구매 가능 크레딧에서 제외되었습니다."),
    ).not.toBeInTheDocument();
  });

  it("구매 가능 여부는 balance가 아니라 available로 판정한다 — available=0이면 held가 있어도 구매 불가로 표시", () => {
    render(
      <WalletBalanceCard
        balance={fullBalance({
          balance: "2000",
          available: "0",
          held: "1500",
          pendingPayout: "500",
        })}
      />,
    );
    const stat = screen.getByText("0 크레딧");
    expect(stat.className).toContain("text-danger");
  });

  it("available/held/pendingPayout이 없는 구버전 서버 응답은 balance만으로 표시한다(폴백)", () => {
    const legacy = { userId: "u-1", balance: "5000" } as WalletBalance;
    render(<WalletBalanceCard balance={legacy} />);
    expect(screen.getByText("5,000 크레딧")).toBeInTheDocument();
    expect(screen.queryByText("주문 보류")).not.toBeInTheDocument();
  });

  it("balance != available+held+pendingPayout 불일치는 조용히 감추지 않고 경고로 표기한다", () => {
    render(<WalletBalanceCard balance={fullBalance({ pendingPayout: "500" })} />);
    expect(screen.getByText(/잔액 데이터에 이상이 감지되었습니다/)).toBeInTheDocument();
    expect(screen.getByText(/SUM_MISMATCH/)).toBeInTheDocument();
  });

  it("음수 금액 응답은 조용히 감추지 않고 경고로 표기한다", () => {
    render(
      <WalletBalanceCard
        balance={fullBalance({ balance: "-500", available: "-500", held: "0", pendingPayout: "0" })}
      />,
    );
    expect(screen.getByText(/NEGATIVE_AMOUNT/)).toBeInTheDocument();
  });

  it("필드 누락 등으로 판독 불가한 응답은 예외 없이 오류 상태를 보여준다", () => {
    const malformed = { userId: "u-1" } as WalletBalance;
    render(<WalletBalanceCard balance={malformed} />);
    expect(screen.getByText(/잔액 정보를 표시할 수 없습니다/)).toBeInTheDocument();
  });
});
