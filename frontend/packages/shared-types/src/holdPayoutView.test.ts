import { describe, expect, it } from "vitest";
import { parseHoldView, parsePayoutBatchView } from "./holdPayoutView";

const HOLD = {
  hold_id: "h-1",
  account_code: "USER:u-1:HELD",
  amount: "1000.00",
  purpose: "purchase",
  reference: "purchase:123",
  state: "PENDING",
  expires_at: "2026-09-10T00:00:00Z",
  entry_id: "e-1",
  schema_version: "v1",
};

const PAYOUT_BATCH = {
  batch_id: "b-1",
  seller_user_id: "u-2",
  period_start: "2026-08-01",
  period_end: "2026-08-31",
  amount: "5000.00",
  state: "SCHEDULED",
  capture_entry_ids: ["e-2", "e-3"],
  release_entry_id: null,
  paid_entry_id: null,
  schema_version: "v1",
};

describe("parseHoldView", () => {
  it("§3.3 필드를 문자열 Decimal 그대로 보존한다", () => {
    expect(parseHoldView(HOLD)).toEqual({ kind: "ok", value: HOLD });
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    expect(parseHoldView({ data: HOLD, meta: { trace_id: "t1" } })).toEqual({
      kind: "ok",
      value: HOLD,
    });
  });

  it.each(["PENDING", "CAPTURED", "RELEASED", "EXPIRED"] as const)(
    "§4.5 상태기계의 %s 상태를 그대로 보존한다",
    (state) => {
      const view = { ...HOLD, state };
      expect(parseHoldView(view)).toEqual({ kind: "ok", value: view });
    },
  );

  it("negative: 필드 누락(entry_id 없음)이면 invalid이다", () => {
    const { entry_id: _drop, ...missing } = HOLD;
    expect(parseHoldView(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: §4.5 화이트리스트 밖 state이면 invalid이다", () => {
    expect(parseHoldView({ ...HOLD, state: "CANCELLED" })).toEqual({ kind: "invalid" });
  });

  it("negative: amount가 Number로 샌 경우 invalid이다", () => {
    expect(parseHoldView({ ...HOLD, amount: 1000 })).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 v1이 아니면 예외 없이 unsupported_schema_version을 반환한다", () => {
    expect(parseHoldView({ ...HOLD, schema_version: "v2" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v2",
    });
  });

  it("negative: schema_version 필드가 아예 없으면 unsupported_schema_version(received=undefined)을 반환한다", () => {
    const { schema_version: _drop, ...withoutVersion } = HOLD;
    expect(parseHoldView(withoutVersion)).toEqual({
      kind: "unsupported_schema_version",
      received: undefined,
    });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parseHoldView(null)).not.toThrow();
    expect(parseHoldView(null)).toEqual({ kind: "invalid" });
    expect(parseHoldView(undefined)).toEqual({ kind: "invalid" });
  });
});

describe("parsePayoutBatchView", () => {
  it("§3.3 필드를 문자열 Decimal 그대로 보존한다", () => {
    expect(parsePayoutBatchView(PAYOUT_BATCH)).toEqual({ kind: "ok", value: PAYOUT_BATCH });
  });

  it("release_entry_id/paid_entry_id가 채워진 완료 배치도 보존한다", () => {
    const paid = {
      ...PAYOUT_BATCH,
      state: "PAID",
      release_entry_id: "e-4",
      paid_entry_id: "e-5",
    };
    expect(parsePayoutBatchView(paid)).toEqual({ kind: "ok", value: paid });
  });

  it.each(["SCHEDULED", "RELEASED", "PAID", "FAILED"] as const)(
    "정산 배치 상태 %s를 그대로 보존한다",
    (state) => {
      const view = { ...PAYOUT_BATCH, state };
      expect(parsePayoutBatchView(view)).toEqual({ kind: "ok", value: view });
    },
  );

  it("negative: capture_entry_ids 누락이면 invalid이다", () => {
    const { capture_entry_ids: _drop, ...missing } = PAYOUT_BATCH;
    expect(parsePayoutBatchView(missing)).toEqual({ kind: "invalid" });
  });

  it("negative: capture_entry_ids 안에 문자열이 아닌 값이 섞이면 invalid이다", () => {
    expect(parsePayoutBatchView({ ...PAYOUT_BATCH, capture_entry_ids: ["e-2", 3] })).toEqual({
      kind: "invalid",
    });
  });

  it("negative: 화이트리스트 밖 state이면 invalid이다", () => {
    expect(parsePayoutBatchView({ ...PAYOUT_BATCH, state: "CANCELLED" })).toEqual({ kind: "invalid" });
  });

  it("negative: amount가 Number로 샌 경우 invalid이다", () => {
    expect(parsePayoutBatchView({ ...PAYOUT_BATCH, amount: 5000 })).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 다르면 unsupported_schema_version이다", () => {
    expect(parsePayoutBatchView({ ...PAYOUT_BATCH, schema_version: "v0" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v0",
    });
  });

  it("negative: 응답이 없으면(null) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parsePayoutBatchView(null)).not.toThrow();
    expect(parsePayoutBatchView(null)).toEqual({ kind: "invalid" });
  });
});
