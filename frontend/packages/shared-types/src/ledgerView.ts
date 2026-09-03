// src/foundation/ledger/contracts/v1.py(§3.3 (C)) 중 JournalEntryView/PostingLine/
// BalanceView 1:1 대응. 서버 라우트가 아직 없으므로 이 모듈은 계약 파싱만
// 담당한다(엔드포인트 추측·mock 서버 금지, task-628과 같은 decision). task-618이
// 지갑 잔액 화면(walletView.ts 부재, WalletBalanceCard 사용)을 작업 중이므로 그
// 경로는 건드리지 않는다.
//
// 금액은 원시 Decimal이 아니라 문자열로 넘어온다 — Number 변환은 부동소수 오차를
// 만들므로 절대 하지 않는다(§3.4 원장 금액은 NUMERIC(20,2) quantize). Σ차변=Σ대변
// 비교도 walletBalance.ts의 decimalSumEquals와 같은 BigInt 스케일 비교 기법을 쓴다.
//
// parseReadiness/parsePositionSnapshot과 같은 컨벤션: 예외를 던지지 않고 판별
// 가능한 결과 객체를 반환한다. schema_version이 "v1"이 아니면(누락 포함) 구조
// 검증보다 먼저 unsupported_schema_version으로 거부한다. PostingLine은 v1.py에서
// schema_version이 없는 중첩 값 객체라 그 변형이 없다 — 구조만 검증한다.

// Currency는 positionView.ts가 이미 정의·export한다(§3.2/§3.3 공통 통화 집합) —
// `export *` 충돌을 피하기 위해 여기서는 재정의하지 않고 그대로 가져다 쓴다.
import type { Currency } from "./positionView";

export type Side = "DEBIT" | "CREDIT";

export type LedgerEventType =
  | "TOPUP_CONFIRMED"
  | "HOLD_PLACED"
  | "HOLD_CAPTURED"
  | "HOLD_RELEASED"
  | "REFUND"
  | "CHARGEBACK"
  | "PAYOUT_RELEASE"
  | "PAYOUT_PAID"
  | "MANUAL_ADJUSTMENT";

export interface PostingLine {
  line_no: number;
  account_code: string;
  side: Side;
  amount: string;
  currency: Currency;
}

export interface JournalEntryView {
  entry_id: string;
  sequence_no: number;
  event_type: LedgerEventType;
  event_ref: string;
  idempotency_key: string;
  lines: PostingLine[];
  lines_digest: string;
  prev_hash: string | null;
  entry_hash: string;
  audit_event_id: string;
  posted_at: string;
  replayed: boolean;
}

export interface BalanceView {
  account_code: string;
  balance: string;
  held: string;
  available: string;
  pending_payout: string;
  currency: Currency;
  last_entry_seq: number;
  as_of: string;
}

export type ParsedPostingLine = { kind: "ok"; value: PostingLine } | { kind: "invalid" };

export type ParsedJournalEntryView =
  | { kind: "ok"; value: JournalEntryView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedBalanceView =
  | { kind: "ok"; value: BalanceView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

const LEDGER_EVENT_TYPES: ReadonlySet<string> = new Set([
  "TOPUP_CONFIRMED",
  "HOLD_PLACED",
  "HOLD_CAPTURED",
  "HOLD_RELEASED",
  "REFUND",
  "CHARGEBACK",
  "PAYOUT_RELEASE",
  "PAYOUT_PAID",
  "MANUAL_ADJUSTMENT",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isCurrency(value: unknown): value is Currency {
  return value === "USDT" || value === "KRW";
}

function isSide(value: unknown): value is Side {
  return value === "DEBIT" || value === "CREDIT";
}

function isLedgerEventType(value: unknown): value is LedgerEventType {
  return typeof value === "string" && LEDGER_EVENT_TYPES.has(value);
}

export function isPostingLine(value: unknown): value is PostingLine {
  if (!isRecord(value)) return false;
  return (
    typeof value.line_no === "number" &&
    typeof value.account_code === "string" &&
    isSide(value.side) &&
    typeof value.amount === "string" &&
    isCurrency(value.currency)
  );
}

function isJournalEntryView(value: Record<string, unknown>): boolean {
  return (
    typeof value.entry_id === "string" &&
    typeof value.sequence_no === "number" &&
    isLedgerEventType(value.event_type) &&
    typeof value.event_ref === "string" &&
    typeof value.idempotency_key === "string" &&
    Array.isArray(value.lines) &&
    value.lines.every(isPostingLine) &&
    typeof value.lines_digest === "string" &&
    isNullableString(value.prev_hash) &&
    typeof value.entry_hash === "string" &&
    typeof value.audit_event_id === "string" &&
    typeof value.posted_at === "string" &&
    typeof value.replayed === "boolean"
  );
}

function isBalanceView(value: Record<string, unknown>): boolean {
  return (
    typeof value.account_code === "string" &&
    typeof value.balance === "string" &&
    typeof value.held === "string" &&
    typeof value.available === "string" &&
    typeof value.pending_payout === "string" &&
    isCurrency(value.currency) &&
    typeof value.last_entry_seq === "number" &&
    typeof value.as_of === "string"
  );
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (parseReadiness/parsePositionSnapshot과 같은 관용 — 봉투 도입 여부를 이 파서가
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

export function parseJournalEntryView(raw: unknown): ParsedJournalEntryView {
  return parseSchemaTagged(raw, isJournalEntryView);
}

export function parseBalanceView(raw: unknown): ParsedBalanceView {
  return parseSchemaTagged(raw, isBalanceView);
}

export function parsePostingLine(raw: unknown): ParsedPostingLine {
  if (!isPostingLine(raw)) return { kind: "invalid" };
  return { kind: "ok", value: raw };
}

// ---- Σ차변=Σ대변 검증 (표시 전용, LedgerEntryList가 소비) ----

const DECIMAL_STRING_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

function toScaledBigInt(amount: string, scale: number): bigint | null {
  const match = DECIMAL_STRING_RE.exec(amount);
  if (!match) return null;
  const [, negative, intPart, fracPart = ""] = match;
  if (fracPart.length > scale) return null;
  const magnitude = BigInt(intPart + fracPart.padEnd(scale, "0"));
  return negative ? -magnitude : magnitude;
}

/**
 * 차변 합과 대변 합이 정확히 같은지 부동소수점 없이 비교한다(§4.4 복식부기
 * 항등식, 원장 금액은 §3.4 NUMERIC(20,2)). 금액 형식이 잘못돼 비교 자체가
 * 불가능하면 안전하게 불일치(false)로 취급한다 — 판정 불가를 "정상"으로 조용히
 * 넘기지 않는다.
 */
export function isLinesBalanced(lines: PostingLine[]): boolean {
  let debit = 0n;
  let credit = 0n;
  for (const line of lines) {
    const scaled = toScaledBigInt(line.amount, 2);
    if (scaled === null) return false;
    if (line.side === "DEBIT") debit += scaled;
    else credit += scaled;
  }
  return debit === credit;
}
