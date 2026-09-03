// src/foundation/ledger/contracts/v1.py(§3.3 (C)) 중 HoldView/PayoutBatchView
// 1:1 대응. 서버 라우트가 아직 없으므로 이 모듈은 계약 파싱만 담당한다
// (엔드포인트 추측·mock 서버 금지, ledgerView.ts/task-628과 같은 decision).
//
// 금액은 원시 Decimal이 아니라 문자열로 넘어온다 — Number 변환은 부동소수 오차를
// 만들므로 절대 하지 않는다(§3.4 원장 금액은 NUMERIC(20,2) quantize).
//
// ledgerView.ts와 같은 컨벤션: 예외를 던지지 않고 판별 가능한 결과 객체를
// 반환한다. schema_version이 "v1"이 아니면(누락 포함) 구조 검증보다 먼저
// unsupported_schema_version으로 거부한다.
//
// §4.5 홀드 상태기계·정산 배치 전이는 서버(ledger_hold/payouts.py)가 유일한
// 소관이다 — 이 모듈과 HoldStatusBadge는 전이를 계산하거나 낙관적으로 갱신하지
// 않고 서버가 내려준 state를 그대로 표시한다(decision, task-658).

export type HoldState = "PENDING" | "CAPTURED" | "RELEASED" | "EXPIRED";

export type PayoutBatchState = "SCHEDULED" | "RELEASED" | "PAID" | "FAILED";

export interface HoldView {
  hold_id: string;
  account_code: string;
  amount: string;
  purpose: string;
  reference: string;
  state: HoldState;
  expires_at: string;
  entry_id: string;
}

export interface PayoutBatchView {
  batch_id: string;
  seller_user_id: string;
  period_start: string;
  period_end: string;
  amount: string;
  state: PayoutBatchState;
  capture_entry_ids: string[];
  release_entry_id: string | null;
  paid_entry_id: string | null;
}

export type ParsedHoldView =
  | { kind: "ok"; value: HoldView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedPayoutBatchView =
  | { kind: "ok"; value: PayoutBatchView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

const HOLD_STATES: readonly HoldState[] = ["PENDING", "CAPTURED", "RELEASED", "EXPIRED"];
const PAYOUT_BATCH_STATES: readonly PayoutBatchState[] = ["SCHEDULED", "RELEASED", "PAID", "FAILED"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isOneOf<T extends string>(allowed: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isHoldState(value: unknown): value is HoldState {
  return isOneOf(HOLD_STATES, value);
}

function isPayoutBatchState(value: unknown): value is PayoutBatchState {
  return isOneOf(PAYOUT_BATCH_STATES, value);
}

function isHoldViewBody(value: Record<string, unknown>): boolean {
  return (
    typeof value.hold_id === "string" &&
    typeof value.account_code === "string" &&
    typeof value.amount === "string" &&
    typeof value.purpose === "string" &&
    typeof value.reference === "string" &&
    isHoldState(value.state) &&
    typeof value.expires_at === "string" &&
    typeof value.entry_id === "string"
  );
}

function isPayoutBatchViewBody(value: Record<string, unknown>): boolean {
  return (
    typeof value.batch_id === "string" &&
    typeof value.seller_user_id === "string" &&
    typeof value.period_start === "string" &&
    typeof value.period_end === "string" &&
    typeof value.amount === "string" &&
    isPayoutBatchState(value.state) &&
    isStringArray(value.capture_entry_ids) &&
    isNullableString(value.release_entry_id) &&
    isNullableString(value.paid_entry_id)
  );
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (ledgerView.ts/instrumentView.ts와 같은 관용 — 봉투 도입 여부를 이 파서가
 * 단정하지 않는다). */
function unwrapEnvelope(raw: unknown): unknown {
  if (isRecord(raw) && "data" in raw) return raw.data;
  return raw;
}

function parseSchemaTagged<T>(
  raw: unknown,
  isView: (value: Record<string, unknown>) => boolean,
): { kind: "ok"; value: T } | { kind: "unsupported_schema_version"; received: unknown } | { kind: "invalid" } {
  const candidate = unwrapEnvelope(raw);
  if (!isRecord(candidate)) return { kind: "invalid" };
  if (candidate.schema_version !== "v1") {
    return { kind: "unsupported_schema_version", received: candidate.schema_version };
  }
  if (!isView(candidate)) return { kind: "invalid" };
  return { kind: "ok", value: candidate as unknown as T };
}

export function parseHoldView(raw: unknown): ParsedHoldView {
  return parseSchemaTagged(raw, isHoldViewBody);
}

export function parsePayoutBatchView(raw: unknown): ParsedPayoutBatchView {
  return parseSchemaTagged(raw, isPayoutBatchViewBody);
}
