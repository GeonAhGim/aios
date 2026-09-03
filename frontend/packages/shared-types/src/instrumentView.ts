// src/foundation/market_data/contracts/v1.py(§3.1 (A) InstrumentRef) 1:1 대응.
// SymbolAlias는 계약(v1.py)에 없는 md_symbol_alias 테이블 행(LA-10 마이그레이션
// 4a1d0c0de007)의 공개 형태다 — 아직 조회 엔드포인트가 없으므로(LA-9 포트에
// list_aliases가 없다) schema_version 태그를 강제하지 않는다(InstrumentView와
// 달리 SSOT pydantic 모델이 아니다). 서버 라우트가 아직 이전되지 않았으므로
// (candleSeries.ts/task-629 decision과 동일) 이 모듈은 계약 파싱만 담당한다.
//
// tick_size/lot_size는 원시 Decimal이 아니라 문자열로 넘어온다 — Number 변환은
// 부동소수 오차를 만들므로 절대 하지 않는다(positionView.ts/candleSeries.ts와
// 동일 원칙).
//
// 클라이언트는 생애주기 전이를 재계산하지 않는다(§4.2 상태기계는 서버 소관) —
// status는 서버 값을 그대로 통과시킨다. alias 유효기간이 만료됐는데도
// status가 LISTED인 모순 입력이라도 이 파서는 invalid로 거부하지 않는다;
// 그 판단(확인 필요 표기)은 표시 컴포넌트(InstrumentLifecycleBadge.tsx) 몫이다.
// 이 파서가 거부하는 것은 구조적으로 불가능한 입력뿐이다 — 화이트리스트 밖
// status 문자열(§4.2: PENDING/LISTED/SUSPENDED/DELISTED 외)과 valid_to <
// valid_from(SymbolAlias, 음수 길이 구간)이 그것이다.
//
// parseCandleSeries와 같은 컨벤션: 예외를 던지지 않고 판별 가능한 결과 객체를
// 반환한다. schema_version이 "v1"이 아니면(누락 포함) 구조 검증보다 먼저
// unsupported_schema_version으로 거부한다.

// Venue는 candleSeries.ts(§3.1 SeriesKey.venue)와 동일한 화이트리스트다 —
// 이 모듈에서 다시 정의하면 배럴(index.ts) re-export가 충돌한다.
export type { Venue } from "./candleSeries";
import type { Venue } from "./candleSeries";

// exchange.ts에도 동명의 AssetClass가 있으나(거래소 능력 고지용, 6종만
// 부분 포함) 여기서는 src/data/models/base.py의 전체 자산군(11종, 해외
// 선물/옵션 포함)을 그대로 옮긴다 — 같은 이름이면 배럴(index.ts)
// re-export가 충돌하므로 InstrumentAssetClass로 구분한다.
export type InstrumentAssetClass =
  | "CRYPTO"
  | "KR_EQUITY"
  | "KR_ETF"
  | "KR_ETN"
  | "KR_FUTURES"
  | "KR_OPTION"
  | "US_EQUITY"
  | "US_ETF"
  | "US_ETN"
  | "OVERSEAS_FUTURES"
  | "OVERSEAS_OPTION";

/** §4.2 심볼 생애주기 상태기계의 상태 화이트리스트. 그 외 문자열은 invalid. */
export type SymbolStatus = "PENDING" | "LISTED" | "SUSPENDED" | "DELISTED";

export interface InstrumentView {
  instrument_id: string;
  venue: Venue;
  canonical_symbol: string;
  venue_symbol: string;
  asset_class: InstrumentAssetClass;
  base: string | null;
  quote: string | null;
  tick_size: string;
  lot_size: string;
  status: SymbolStatus;
  listed_at: string;
  delisted_at: string | null;
}

/** md_symbol_alias 행. valid_to=null은 "현재 유효"를 뜻한다(테이블의
 * EXCLUDE USING gist가 COALESCE(valid_to, 'infinity')로 이를 표현). */
export interface SymbolAlias {
  alias_id: string;
  instrument_id: string;
  venue: Venue;
  alias_symbol: string;
  valid_from: string;
  valid_to: string | null;
}

export type ParsedInstrumentView =
  | { kind: "ok"; value: InstrumentView }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedSymbolAlias = { kind: "ok"; value: SymbolAlias } | { kind: "invalid" };

const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const ASSET_CLASSES: readonly InstrumentAssetClass[] = [
  "CRYPTO",
  "KR_EQUITY",
  "KR_ETF",
  "KR_ETN",
  "KR_FUTURES",
  "KR_OPTION",
  "US_EQUITY",
  "US_ETF",
  "US_ETN",
  "OVERSEAS_FUTURES",
  "OVERSEAS_OPTION",
];
const SYMBOL_STATUSES: readonly SymbolStatus[] = ["PENDING", "LISTED", "SUSPENDED", "DELISTED"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isOneOf<T extends string>(allowed: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isVenue(value: unknown): value is Venue {
  return isOneOf(VENUES, value);
}

function isAssetClass(value: unknown): value is InstrumentAssetClass {
  return isOneOf(ASSET_CLASSES, value);
}

function isSymbolStatus(value: unknown): value is SymbolStatus {
  return isOneOf(SYMBOL_STATUSES, value);
}

function isInstrumentViewBody(value: Record<string, unknown>): boolean {
  return (
    typeof value.instrument_id === "string" &&
    isVenue(value.venue) &&
    typeof value.canonical_symbol === "string" &&
    typeof value.venue_symbol === "string" &&
    isAssetClass(value.asset_class) &&
    isNullableString(value.base) &&
    isNullableString(value.quote) &&
    typeof value.tick_size === "string" &&
    typeof value.lot_size === "string" &&
    isSymbolStatus(value.status) &&
    typeof value.listed_at === "string" &&
    isNullableString(value.delisted_at)
  );
}

/** valid_to가 있으면 valid_from보다 뒤여야 한다(0 길이·역순 구간 거부) — DB의
 * EXCLUDE USING gist는 겹침만 막을 뿐 순서를 강제하지 않아 이 파서가 맡는다. */
function isValidAliasPeriod(validFrom: string, validTo: string | null): boolean {
  if (validTo === null) return true;
  const fromMs = Date.parse(validFrom);
  const toMs = Date.parse(validTo);
  return Number.isFinite(fromMs) && Number.isFinite(toMs) && fromMs < toMs;
}

function isSymbolAliasBody(value: Record<string, unknown>): boolean {
  if (
    typeof value.alias_id !== "string" ||
    typeof value.instrument_id !== "string" ||
    !isVenue(value.venue) ||
    typeof value.alias_symbol !== "string" ||
    typeof value.valid_from !== "string" ||
    !isNullableString(value.valid_to)
  ) {
    return false;
  }
  return isValidAliasPeriod(value.valid_from, value.valid_to as string | null);
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (parseCandleSeries와 같은 관용 — 봉투 도입 여부를 이 파서가 단정하지 않는다). */
function unwrapEnvelope(raw: unknown): unknown {
  if (isRecord(raw) && "data" in raw) return raw.data;
  return raw;
}

export function parseInstrumentView(raw: unknown): ParsedInstrumentView {
  const candidate = unwrapEnvelope(raw);
  if (!isRecord(candidate)) return { kind: "invalid" };
  if (candidate.schema_version !== "v1") {
    return { kind: "unsupported_schema_version", received: candidate.schema_version };
  }
  if (!isInstrumentViewBody(candidate)) return { kind: "invalid" };
  return { kind: "ok", value: candidate as unknown as InstrumentView };
}

/** SymbolAlias는 v1.py SSOT 태그(schema_version)가 없는 DB 행 형태라
 * unsupported_schema_version 분기가 없다(위 파일 주석 참조). */
export function parseSymbolAlias(raw: unknown): ParsedSymbolAlias {
  const candidate = unwrapEnvelope(raw);
  if (!isRecord(candidate)) return { kind: "invalid" };
  if (!isSymbolAliasBody(candidate)) return { kind: "invalid" };
  return { kind: "ok", value: candidate as unknown as SymbolAlias };
}
