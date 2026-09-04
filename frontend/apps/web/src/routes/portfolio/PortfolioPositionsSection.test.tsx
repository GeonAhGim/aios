import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PortfolioPositionsSection } from "./PortfolioPositionsSection";

afterEach(() => cleanup());

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

const NAV = {
  schema_version: "v1",
  account_id: "a-1",
  nav_date: "2026-09-03",
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

const PNL = {
  schema_version: "v1",
  realized: "1000.00",
  unrealized: "2500.00",
  fees: "10.00",
  funding: "0.00",
  total: "3490.00",
  base_currency: "KRW",
  fx_rates_used: [],
};

// spec §3.2 (B): PortfolioPositionsSection은 positionView.ts 파서(parsePositionSnapshot/
// parsePnLBreakdown/parseNavSnapshot, task-628 decision)만 사용해 서버가 준 raw 데이터를
// 렌더한다 — 컴포넌트 안에서 파서를 재구현하지 않는다.
describe("PortfolioPositionsSection 포지션 스냅샷", () => {
  it("포지션이 없으면 빈 상태 문구를 보여준다", () => {
    render(<PortfolioPositionsSection positions={[]} />);

    expect(screen.getByText("포지션 스냅샷이 없습니다.")).toBeInTheDocument();
  });

  it("정상 스냅샷 여러 건은 각 position_key로 카드를 렌더링한다", () => {
    const other = { ...SNAPSHOT, position_key: "upbit:ETH-KRW:strat-1:exec-1" };
    render(<PortfolioPositionsSection positions={[SNAPSHOT, other]} />);

    expect(screen.getByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(screen.getByText("upbit:ETH-KRW:strat-1:exec-1")).toBeInTheDocument();
  });

  it("negative: schema_version이 잘못된 스냅샷은 예외 없이 danger 안내로 렌더링한다", () => {
    const badSchema = { ...SNAPSHOT, schema_version: "v2" };
    expect(() => render(<PortfolioPositionsSection positions={[badSchema]} />)).not.toThrow();
    expect(screen.getByText(/지원하지 않는 schema_version/)).toBeInTheDocument();
  });

  it("negative: 구조가 다른 항목은 예외 없이 danger 안내로 렌더링하고 index 기반 key로 구분한다", () => {
    const invalid = { schema_version: "v1", not_a_snapshot: true };
    expect(() =>
      render(<PortfolioPositionsSection positions={[SNAPSHOT, invalid]} />),
    ).not.toThrow();
    expect(screen.getByText("upbit:BTC-KRW:strat-1:exec-1")).toBeInTheDocument();
    expect(screen.getByText("포지션 데이터를 해석할 수 없습니다.")).toBeInTheDocument();
  });
});

describe("PortfolioPositionsSection NAV 카드", () => {
  it("nav prop이 주어지면 NAV 스냅샷 카드를 보여준다", () => {
    render(<PortfolioPositionsSection positions={[]} nav={NAV} />);

    expect(screen.getByTestId("nav-snapshot-card")).toHaveTextContent("NAV · 2026-09-03");
    expect(screen.getByText("100000.00 KRW")).toBeInTheDocument();
    expect(screen.getByText("101000.00 KRW")).toBeInTheDocument();
  });

  it("nav prop이 없으면 NAV 카드를 렌더링하지 않는다", () => {
    render(<PortfolioPositionsSection positions={[]} />);

    expect(screen.queryByTestId("nav-snapshot-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("nav-snapshot-error")).not.toBeInTheDocument();
  });

  it("negative: NAV schema_version 불일치는 예외 없이 danger 안내로 렌더링한다", () => {
    expect(() =>
      render(<PortfolioPositionsSection positions={[]} nav={{ schema_version: "v2" }} />),
    ).not.toThrow();
    expect(screen.getByTestId("nav-snapshot-error")).toBeInTheDocument();
  });
});

describe("PortfolioPositionsSection PnL 분해 카드", () => {
  it("pnl prop이 주어지면 실현·미실현·수수료·펀딩·합계를 보여준다", () => {
    render(<PortfolioPositionsSection positions={[]} pnl={PNL} />);

    const card = screen.getByTestId("pnl-breakdown-card");
    expect(card).toHaveTextContent("1000.00 KRW");
    expect(card).toHaveTextContent("2500.00 KRW");
    expect(card).toHaveTextContent("3490.00 KRW");
  });

  it("pnl prop이 없으면 PnL 분해 카드를 렌더링하지 않는다", () => {
    render(<PortfolioPositionsSection positions={[]} />);

    expect(screen.queryByTestId("pnl-breakdown-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pnl-breakdown-error")).not.toBeInTheDocument();
  });

  it("negative: PnL 분해 구조가 잘못되면 예외 없이 danger 안내로 렌더링한다", () => {
    expect(() =>
      render(<PortfolioPositionsSection positions={[]} pnl={{ schema_version: "v1" }} />),
    ).not.toThrow();
    expect(screen.getByTestId("pnl-breakdown-error")).toBeInTheDocument();
  });
});

// task-936 decision: as_of 스테일 판정은 deriveFreshness(now 주입)로만 하고
// Date.now()를 대입하지 않는다 — now를 명시적으로 넘겨 결정적으로 검증한다.
describe("PortfolioPositionsSection 데이터 신선도 배너", () => {
  it("as_of가 STALE_AFTER_SEC(300초)보다 오래되면 지연 배너를 보여준다", () => {
    render(
      <PortfolioPositionsSection
        positions={[SNAPSHOT]}
        asOf="2026-09-03T00:00:00Z"
        now={new Date("2026-09-03T00:20:00Z")}
      />,
    );

    expect(screen.getByTestId("positions-stale-banner")).toBeInTheDocument();
  });

  it("as_of가 신선하면 지연 배너를 보여주지 않는다", () => {
    render(
      <PortfolioPositionsSection
        positions={[SNAPSHOT]}
        asOf="2026-09-03T00:00:00Z"
        now={new Date("2026-09-03T00:01:00Z")}
      />,
    );

    expect(screen.queryByTestId("positions-stale-banner")).not.toBeInTheDocument();
  });

  it("asOf가 없으면 신선도를 판정할 수 없어 지연 배너를 보여주지 않는다", () => {
    render(<PortfolioPositionsSection positions={[SNAPSHOT]} />);

    expect(screen.queryByTestId("positions-stale-banner")).not.toBeInTheDocument();
  });
});
