import { describe, expect, it } from "vitest";
import {
  isLinesBalanced,
  parseBalanceView,
  parseJournalEntryView,
  parsePostingLine,
} from "./ledgerView";

const DEBIT_LINE = { line_no: 1, account_code: "USER:u-1:AVAILABLE", side: "DEBIT", amount: "1000.00", currency: "KRW" };
const CREDIT_LINE = { line_no: 2, account_code: "PLATFORM:REVENUE", side: "CREDIT", amount: "1000.00", currency: "KRW" };

const ENTRY = {
  entry_id: "e-1",
  sequence_no: 42,
  event_type: "TOPUP_CONFIRMED",
  event_ref: "topup:45",
  idempotency_key: "idem-1",
  lines: [DEBIT_LINE, CREDIT_LINE],
  lines_digest: "digest-1",
  prev_hash: "hash-0",
  entry_hash: "hash-1",
  audit_event_id: "audit-1",
  posted_at: "2026-09-03T00:00:00Z",
  replayed: false,
  schema_version: "v1",
};

const BALANCE = {
  account_code: "USER:u-1:AVAILABLE",
  balance: "5000.00",
  held: "1000.00",
  available: "3000.00",
  pending_payout: "1000.00",
  currency: "KRW",
  last_entry_seq: 42,
  as_of: "2026-09-03T00:00:00Z",
  schema_version: "v1",
};

describe("parseJournalEntryView", () => {
  it("§3.3 필드를 문자열 Decimal 그대로 보존한다", () => {
    expect(parseJournalEntryView(ENTRY)).toEqual({ kind: "ok", value: ENTRY });
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    expect(parseJournalEntryView({ data: ENTRY, meta: { trace_id: "t1" } })).toEqual({
      kind: "ok",
      value: ENTRY,
    });
  });

  it("negative: prev_hash가 null이어도 유효하다(최초 엔트리)", () => {
    const first = { ...ENTRY, prev_hash: null };
    expect(parseJournalEntryView(first)).toEqual({ kind: "ok", value: first });
  });

  it("negative: 필드 누락(entry_hash 없음)이면 invalid이다", () => {
    const { entry_hash: _drop, ...missing } = ENTRY;
    expect(parseJournalEntryView(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: 잘못된 event_type이면 invalid이다", () => {
    const badType = { ...ENTRY, event_type: "UNKNOWN_EVENT" };
    expect(parseJournalEntryView(badType)).toEqual({ kind: "invalid" });
  });

  it("negative: lines 안에 잘못된 side가 있으면 invalid이다", () => {
    const badSide = { ...ENTRY, lines: [{ ...DEBIT_LINE, side: "DEBTOR" }, CREDIT_LINE] };
    expect(parseJournalEntryView(badSide)).toEqual({ kind: "invalid" });
  });

  it("negative: lines 안의 amount가 Number로 샌 경우 invalid이다", () => {
    const numericAmount = { ...ENTRY, lines: [{ ...DEBIT_LINE, amount: 1000 }, CREDIT_LINE] };
    expect(parseJournalEntryView(numericAmount)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 v1이 아니면 예외 없이 unsupported_schema_version을 반환한다", () => {
    expect(parseJournalEntryView({ ...ENTRY, schema_version: "v2" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v2",
    });
  });

  it("negative: schema_version 필드가 아예 없으면 unsupported_schema_version(received=undefined)을 반환한다", () => {
    const { schema_version: _drop, ...withoutVersion } = ENTRY;
    expect(parseJournalEntryView(withoutVersion)).toEqual({
      kind: "unsupported_schema_version",
      received: undefined,
    });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parseJournalEntryView(null)).not.toThrow();
    expect(parseJournalEntryView(null)).toEqual({ kind: "invalid" });
    expect(parseJournalEntryView(undefined)).toEqual({ kind: "invalid" });
  });
});

describe("parseBalanceView", () => {
  it("§3.3 필드를 문자열 Decimal 그대로 보존한다", () => {
    expect(parseBalanceView(BALANCE)).toEqual({ kind: "ok", value: BALANCE });
  });

  it("negative: pending_payout 누락이면 invalid이다", () => {
    const { pending_payout: _drop, ...missing } = BALANCE;
    expect(parseBalanceView(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: balance가 Number로 샌 경우 invalid이다", () => {
    expect(parseBalanceView({ ...BALANCE, balance: 5000 })).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 다르면 unsupported_schema_version이다", () => {
    expect(parseBalanceView({ ...BALANCE, schema_version: "v0" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v0",
    });
  });
});

describe("parsePostingLine", () => {
  it("차변 라인을 구조 그대로 보존한다(schema_version 필드 없음)", () => {
    expect(parsePostingLine(DEBIT_LINE)).toEqual({ kind: "ok", value: DEBIT_LINE });
  });

  it("negative: account_type이 아니라 side가 잘못된 값이면 invalid이다", () => {
    expect(parsePostingLine({ ...DEBIT_LINE, side: "OTHER" })).toEqual({ kind: "invalid" });
  });

  it("negative: currency가 화이트리스트 밖이면 invalid이다", () => {
    expect(parsePostingLine({ ...DEBIT_LINE, currency: "USD" })).toEqual({ kind: "invalid" });
  });

  it("negative: 응답이 없으면(null) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parsePostingLine(null)).not.toThrow();
    expect(parsePostingLine(null)).toEqual({ kind: "invalid" });
  });
});

describe("isLinesBalanced", () => {
  it("Σ차변=Σ대변이면 true다", () => {
    expect(isLinesBalanced([DEBIT_LINE, CREDIT_LINE])).toBe(true);
  });

  it("Σ차변≠Σ대변이면 false다(합계 불일치, 조용히 통과시키지 않는다)", () => {
    const shortCredit = { ...CREDIT_LINE, amount: "999.00" };
    expect(isLinesBalanced([DEBIT_LINE, shortCredit])).toBe(false);
  });

  it("여러 라인의 합도 부동소수점 오차 없이 정확히 비교한다", () => {
    const d1 = { ...DEBIT_LINE, line_no: 1, amount: "0.10" };
    const d2 = { ...DEBIT_LINE, line_no: 2, amount: "0.20" };
    const c1 = { ...CREDIT_LINE, line_no: 3, amount: "0.30" };
    expect(isLinesBalanced([d1, d2, c1])).toBe(true);
  });

  it("금액 형식이 잘못돼 비교 불가하면 안전하게 불일치(false)로 취급한다", () => {
    const malformed = { ...DEBIT_LINE, amount: "not-a-number" };
    expect(isLinesBalanced([malformed, CREDIT_LINE])).toBe(false);
  });

  it("빈 라인 목록은 0=0이므로 true다", () => {
    expect(isLinesBalanced([])).toBe(true);
  });
});
