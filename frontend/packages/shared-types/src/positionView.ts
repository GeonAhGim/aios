// src/foundation/positions/contracts/v1.py(§3.2 (B)) 1:1 대응. 서버 라우트가 아직
// 없으므로 이 모듈은 계약 파싱만 담당한다(엔드포인트 추측 금지, task-628 decision).
//
// 금액·수량은 원시 Decimal이 아니라 문자열로 넘어온다 — Number 변환은 부동소수
// 오차를 만들므로 절대 하지 않는다(§3.4). unrealized_pnl_base는 mark_price 부재
// (POS_MARK_STALE)면 null이며, 이는 "0"과 다른 사실이다 — 0으로 뭉개지 않고
// 그대로 보존해 화면(PositionPnLCard)이 "평가 불가"로 구분 표기하게 한다.
//
// parseReadiness(readiness.ts)와 같은 컨벤션: 예외를 던지지 않고 판별 가능한
// 결과 객체를 반환한다. schema_version이 "v1"이 아니면(누락 포함) 구조 검증보다
// 먼저 unsupported_schema_version으로 거부한다.

export type Currency = "USDT" | "KRW";

export interface Money {
  amount: string;
  currency: Currency;
}

export interface FXRate {
  base: Currency;
  quote: Currency;
  rate: string;
  timestamp: string;
  source: string;
}

export type CostMethod = "FIFO" | "WEIGHTED";

export interface Lot {
  quantity: string;
  unit_cost: string;
  opened_at: string;
}

/** 저널의 fold 결과(§4.3). quantity는 계약 그대로 부호 제한 없이 보존한다
 * (숏 허용 여부는 이 파서가 판단하지 않는다). */
export interface PositionSnapshotView {
  position_key: string;
  tenant_id: string;
  account_id: string;
  instrument_id: string;
  quantity: string;
  avg_cost: Money;
  cost_method: CostMethod;
  lots: Lot[];
  realized_pnl_base: string;
  unrealized_pnl_base: string | null;
  fees_base: string;
  funding_base: string;
  mark_price: Money | null;
  mark_at: string | null;
  base_currency: Currency;
  last_journal_seq: number;
  updated_at: string;
}

export interface PnLBreakdown {
  realized: string;
  unrealized: string;
  fees: string;
  funding: string;
  total: string;
  base_currency: Currency;
  fx_rates_used: FXRate[];
}

export interface NAVSnapshot {
  account_id: string;
  nav_date: string;
  base_currency: Currency;
  opening_nav: string;
  cash: string;
  positions_mv: string;
  realized: string;
  unrealized_delta: string;
  funding: string;
  fees: string;
  flows: string;
  closing_nav: string;
  fx_rates: FXRate[];
  source_hash: string;
}

export type ParsedPositionSnapshot =
  | { kind: "ok"; value: PositionSnapshotView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedPnLBreakdown =
  | { kind: "ok"; value: PnLBreakdown }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedNavSnapshot =
  | { kind: "ok"; value: NAVSnapshot }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isCurrency(value: unknown): value is Currency {
  return value === "USDT" || value === "KRW";
}

function isCostMethod(value: unknown): value is CostMethod {
  return value === "FIFO" || value === "WEIGHTED";
}

function isMoney(value: unknown): value is Money {
  if (!isRecord(value)) return false;
  return typeof value.amount === "string" && isCurrency(value.currency);
}

function isNullableMoney(value: unknown): value is Money | null {
  return value === null || value === undefined || isMoney(value);
}

function isFXRate(value: unknown): value is FXRate {
  if (!isRecord(value)) return false;
  return (
    isCurrency(value.base) &&
    isCurrency(value.quote) &&
    typeof value.rate === "string" &&
    typeof value.timestamp === "string" &&
    typeof value.source === "string"
  );
}

function isLot(value: unknown): value is Lot {
  if (!isRecord(value)) return false;
  return (
    typeof value.quantity === "string" &&
    typeof value.unit_cost === "string" &&
    typeof value.opened_at === "string"
  );
}

function isPositionSnapshotView(value: Record<string, unknown>): boolean {
  return (
    typeof value.position_key === "string" &&
    typeof value.tenant_id === "string" &&
    typeof value.account_id === "string" &&
    typeof value.instrument_id === "string" &&
    typeof value.quantity === "string" &&
    isMoney(value.avg_cost) &&
    isCostMethod(value.cost_method) &&
    Array.isArray(value.lots) &&
    value.lots.every(isLot) &&
    typeof value.realized_pnl_base === "string" &&
    isNullableString(value.unrealized_pnl_base) &&
    typeof value.fees_base === "string" &&
    typeof value.funding_base === "string" &&
    isNullableMoney(value.mark_price) &&
    isNullableString(value.mark_at) &&
    isCurrency(value.base_currency) &&
    typeof value.last_journal_seq === "number" &&
    typeof value.updated_at === "string"
  );
}

function isPnLBreakdown(value: Record<string, unknown>): boolean {
  return (
    typeof value.realized === "string" &&
    typeof value.unrealized === "string" &&
    typeof value.fees === "string" &&
    typeof value.funding === "string" &&
    typeof value.total === "string" &&
    isCurrency(value.base_currency) &&
    Array.isArray(value.fx_rates_used) &&
    value.fx_rates_used.every(isFXRate)
  );
}

function isNavSnapshot(value: Record<string, unknown>): boolean {
  return (
    typeof value.account_id === "string" &&
    typeof value.nav_date === "string" &&
    isCurrency(value.base_currency) &&
    typeof value.opening_nav === "string" &&
    typeof value.cash === "string" &&
    typeof value.positions_mv === "string" &&
    typeof value.realized === "string" &&
    typeof value.unrealized_delta === "string" &&
    typeof value.funding === "string" &&
    typeof value.fees === "string" &&
    typeof value.flows === "string" &&
    typeof value.closing_nav === "string" &&
    Array.isArray(value.fx_rates) &&
    value.fx_rates.every(isFXRate) &&
    typeof value.source_hash === "string"
  );
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (parseReadiness와 같은 관용 — 봉투 도입 여부를 이 파서가 단정하지 않는다). */
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

export function parsePositionSnapshot(raw: unknown): ParsedPositionSnapshot {
  return parseSchemaTagged(raw, isPositionSnapshotView);
}

export function parsePnLBreakdown(raw: unknown): ParsedPnLBreakdown {
  return parseSchemaTagged(raw, isPnLBreakdown);
}

export function parseNavSnapshot(raw: unknown): ParsedNavSnapshot {
  return parseSchemaTagged(raw, isNavSnapshot);
}
