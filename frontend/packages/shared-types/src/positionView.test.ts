import { describe, expect, it } from "vitest";
import { parseNavSnapshot, parsePnLBreakdown, parsePositionSnapshot } from "./positionView";

const LOT = { quantity: "1.5", unit_cost: "50000.00", opened_at: "2026-09-01T00:00:00Z" };

const SNAPSHOT = {
  position_key: "upbit:BTC-KRW:strat-1:exec-1",
  tenant_id: "t-1",
  account_id: "a-1",
  instrument_id: "i-1",
  quantity: "1.5",
  avg_cost: { amount: "50000.00", currency: "KRW" },
  cost_method: "FIFO",
  lots: [LOT],
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

const FX_RATE = { base: "USDT", quote: "KRW", rate: "1350.00", timestamp: "2026-09-03T00:00:00Z", source: "test" };

const PNL = {
  realized: "1000.00",
  unrealized: "2500.00",
  fees: "10.00",
  funding: "0.00",
  total: "3490.00",
  base_currency: "KRW",
  fx_rates_used: [FX_RATE],
  schema_version: "v1",
};

const NAV = {
  account_id: "a-1",
  nav_date: "2026-09-03",
  base_currency: "KRW",
  opening_nav: "100000.00",
  cash: "50000.00",
  positions_mv: "51666.67",
  realized: "1000.00",
  unrealized_delta: "500.00",
  funding: "0.00",
  fees: "10.00",
  flows: "0.00",
  closing_nav: "101666.67",
  fx_rates: [FX_RATE],
  source_hash: "deadbeef",
  schema_version: "v1",
};

describe("parsePositionSnapshot", () => {
  it("§3.2 필드를 문자열 Decimal 그대로 보존한다", () => {
    const parsed = parsePositionSnapshot(SNAPSHOT);
    expect(parsed).toEqual({ kind: "ok", value: SNAPSHOT });
    if (parsed.kind === "ok") {
      expect(typeof parsed.value.quantity).toBe("string");
      expect(typeof parsed.value.avg_cost.amount).toBe("string");
    }
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    const parsed = parsePositionSnapshot({ data: SNAPSHOT, meta: { trace_id: "t1" } });
    expect(parsed).toEqual({ kind: "ok", value: SNAPSHOT });
  });

  it("mark price 부재로 unrealized_pnl_base가 null이어도 0으로 대체하지 않고 그대로 보존한다", () => {
    const stale = { ...SNAPSHOT, unrealized_pnl_base: null, mark_price: null, mark_at: null };
    const parsed = parsePositionSnapshot(stale);
    expect(parsed).toEqual({ kind: "ok", value: stale });
    if (parsed.kind === "ok") {
      expect(parsed.value.unrealized_pnl_base).toBeNull();
    }
  });

  it("음수 quantity(숏)는 계약이 금지하지 않으므로 거부하지 않고 그대로 통과시킨다", () => {
    const short = { ...SNAPSHOT, quantity: "-1.5" };
    const parsed = parsePositionSnapshot(short);
    expect(parsed).toEqual({ kind: "ok", value: short });
  });

  it("negative: schema_version이 v1이 아니면 예외 없이 unsupported_schema_version을 반환한다", () => {
    const v2 = { ...SNAPSHOT, schema_version: "v2" };
    expect(parsePositionSnapshot(v2)).toEqual({ kind: "unsupported_schema_version", received: "v2" });
  });

  it("negative: schema_version 필드가 아예 없으면 unsupported_schema_version(received=undefined)을 반환한다", () => {
    const { schema_version: _drop, ...withoutVersion } = SNAPSHOT;
    expect(parsePositionSnapshot(withoutVersion)).toEqual({
      kind: "unsupported_schema_version",
      received: undefined,
    });
  });

  it("negative: 필드 누락(avg_cost 없음)이면 invalid이다", () => {
    const { avg_cost: _drop, ...missing } = SNAPSHOT;
    expect(parsePositionSnapshot(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: 잘못된 cost_method면 invalid이다", () => {
    const badMethod = { ...SNAPSHOT, cost_method: "AVERAGE" };
    expect(parsePositionSnapshot(badMethod)).toEqual({ kind: "invalid" });
  });

  it("negative: mark_price가 있는데 amount가 숫자 타입이면(Decimal이 Number로 샌 경우) invalid이다", () => {
    const numericAmount = { ...SNAPSHOT, avg_cost: { amount: 50000, currency: "KRW" } };
    expect(parsePositionSnapshot(numericAmount)).toEqual({ kind: "invalid" });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parsePositionSnapshot(null)).not.toThrow();
    expect(parsePositionSnapshot(null)).toEqual({ kind: "invalid" });
    expect(parsePositionSnapshot(undefined)).toEqual({ kind: "invalid" });
  });
});

describe("parsePnLBreakdown", () => {
  it("§3.2 PnLBreakdown 필드를 문자열 그대로 보존한다", () => {
    expect(parsePnLBreakdown(PNL)).toEqual({ kind: "ok", value: PNL });
  });

  it("negative: fx_rates_used 항목이 스키마를 어기면 invalid이다", () => {
    const badFx = { ...PNL, fx_rates_used: [{ ...FX_RATE, rate: 1350 }] };
    expect(parsePnLBreakdown(badFx)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 다르면 unsupported_schema_version이다", () => {
    expect(parsePnLBreakdown({ ...PNL, schema_version: "v0" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v0",
    });
  });
});

describe("parseNavSnapshot", () => {
  it("§3.2 NAVSnapshot 필드를 문자열 그대로 보존한다", () => {
    expect(parseNavSnapshot(NAV)).toEqual({ kind: "ok", value: NAV });
  });

  it("negative: closing_nav 누락이면 invalid이다", () => {
    const { closing_nav: _drop, ...missing } = NAV;
    expect(parseNavSnapshot(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 다르면 unsupported_schema_version이다", () => {
    expect(parseNavSnapshot({ ...NAV, schema_version: "v2" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v2",
    });
  });
});
