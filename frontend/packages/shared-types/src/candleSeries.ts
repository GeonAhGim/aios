// src/foundation/market_data/contracts/v1.py(§3.1 (A)) 1:1 대응. 서버 라우트가
// 아직 이전되지 않았으므로(LA-13/LA-17 이전, task-629 decision) 이 모듈은 계약
// 파싱만 담당한다(엔드포인트 호출·차트 라이브러리 도입 금지).
//
// OHLC·거래량은 원시 Decimal이 아니라 문자열로 넘어온다 — Number 변환은 부동소수
// 오차를 만들므로 절대 하지 않는다(positionView.ts와 동일 원칙). 타임스탬프는
// UTC ISO 문자열 그대로 보존하고, 순서 비교(gap 구간 검증)에만 Date.parse를 쓴다.
//
// parsePositionSnapshot과 같은 컨벤션: 예외를 던지지 않고 판별 가능한 결과
// 객체를 반환한다. schema_version이 "v1"이 아니면(누락 포함) 구조 검증보다
// 먼저 unsupported_schema_version으로 거부하고, 화이트리스트 밖 enum 값(timeframe/
// venue/verdict/severity 등)은 invalid로 거부한다.

export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d";
export type Venue = "BITGET" | "KIS_KRX" | "KIS_US";
export type Adjustment = "RAW" | "ADJUSTED";

export type QualityIssueType =
  | "OHLC_INCONSISTENT"
  | "NEGATIVE_VOLUME"
  | "TIME_MISALIGNED"
  | "NAIVE_DATETIME"
  | "GAP"
  | "STALE"
  | "SPIKE"
  | "DUPLICATE_IDENTICAL"
  | "DUPLICATE_CONFLICT"
  | "OUT_OF_SESSION";

export type Severity = "INFO" | "WARN" | "REJECT";
export type Verdict = "ACCEPT" | "PARTIAL" | "QUARANTINE" | "REJECT";

export interface SeriesKey {
  venue: Venue;
  instrument_id: string;
  timeframe: Timeframe;
}

export interface CandleRecord {
  key: SeriesKey;
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quote_volume: string | null;
}

/** [gap_start, gap_end) — 결측 구간(open_time 기준). 시작은 끝보다 앞서야 한다. */
export type CandleGap = readonly [string, string];

export interface CandleSeries {
  key: SeriesKey;
  candles: CandleRecord[];
  gaps: CandleGap[];
  adjustment: Adjustment;
  as_of: string;
  series_hash: string;
}

export interface QualityIssue {
  type: QualityIssueType;
  severity: Severity;
  open_time: string | null;
  detail: Record<string, string>;
}

export interface QualityVerdict {
  verdict: Verdict;
  accepted: number;
  quarantined: number;
  rejected: number;
  issues: QualityIssue[];
}

export type ParsedCandleSeries =
  | { kind: "ok"; value: CandleSeries }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

export type ParsedQualityVerdict =
  | { kind: "ok"; value: QualityVerdict }
  | { kind: "unsupported_schema_version"; received: unknown }
  | { kind: "invalid" };

const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const ADJUSTMENTS: readonly Adjustment[] = ["RAW", "ADJUSTED"];
const QUALITY_ISSUE_TYPES: readonly QualityIssueType[] = [
  "OHLC_INCONSISTENT",
  "NEGATIVE_VOLUME",
  "TIME_MISALIGNED",
  "NAIVE_DATETIME",
  "GAP",
  "STALE",
  "SPIKE",
  "DUPLICATE_IDENTICAL",
  "DUPLICATE_CONFLICT",
  "OUT_OF_SESSION",
];
const SEVERITIES: readonly Severity[] = ["INFO", "WARN", "REJECT"];
const VERDICTS: readonly Verdict[] = ["ACCEPT", "PARTIAL", "QUARANTINE", "REJECT"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isOneOf<T extends string>(allowed: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

function isTimeframe(value: unknown): value is Timeframe {
  return isOneOf(TIMEFRAMES, value);
}

function isVenue(value: unknown): value is Venue {
  return isOneOf(VENUES, value);
}

function isAdjustment(value: unknown): value is Adjustment {
  return isOneOf(ADJUSTMENTS, value);
}

function isQualityIssueType(value: unknown): value is QualityIssueType {
  return isOneOf(QUALITY_ISSUE_TYPES, value);
}

function isSeverity(value: unknown): value is Severity {
  return isOneOf(SEVERITIES, value);
}

function isVerdict(value: unknown): value is Verdict {
  return isOneOf(VERDICTS, value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isSeriesKey(value: unknown): value is SeriesKey {
  if (!isRecord(value)) return false;
  return isVenue(value.venue) && typeof value.instrument_id === "string" && isTimeframe(value.timeframe);
}

function isCandleRecord(value: unknown): value is CandleRecord {
  if (!isRecord(value)) return false;
  return (
    isSeriesKey(value.key) &&
    typeof value.open_time === "string" &&
    typeof value.close_time === "string" &&
    typeof value.open === "string" &&
    typeof value.high === "string" &&
    typeof value.low === "string" &&
    typeof value.close === "string" &&
    typeof value.volume === "string" &&
    isNullableString(value.quote_volume)
  );
}

function isChronological(start: string, end: string): boolean {
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  return Number.isFinite(startMs) && Number.isFinite(endMs) && startMs < endMs;
}

function isCandleGap(value: unknown): value is CandleGap {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "string" &&
    typeof value[1] === "string" &&
    isChronological(value[0], value[1])
  );
}

function isCandleSeriesBody(value: Record<string, unknown>): boolean {
  return (
    isSeriesKey(value.key) &&
    Array.isArray(value.candles) &&
    value.candles.every(isCandleRecord) &&
    Array.isArray(value.gaps) &&
    value.gaps.every(isCandleGap) &&
    isAdjustment(value.adjustment) &&
    typeof value.as_of === "string" &&
    typeof value.series_hash === "string"
  );
}

function isQualityIssue(value: unknown): value is QualityIssue {
  if (!isRecord(value)) return false;
  if (!isQualityIssueType(value.type)) return false;
  if (!isSeverity(value.severity)) return false;
  if (!isNullableString(value.open_time)) return false;
  if (!isRecord(value.detail)) return false;
  return Object.values(value.detail).every((entry) => typeof entry === "string");
}

/** ACCEPT는 격리·거부가 전혀 없었다는 뜻이다(§4.1 fail-closed 판정표) — quarantined
 * 또는 rejected가 0보다 크면서 verdict=ACCEPT인 입력은 모순이므로 거부한다. */
function isConsistentVerdict(verdict: Verdict, quarantined: number, rejected: number): boolean {
  if (verdict === "ACCEPT") return quarantined === 0 && rejected === 0;
  return true;
}

function isQualityVerdictBody(value: Record<string, unknown>): boolean {
  if (
    !isVerdict(value.verdict) ||
    !isNonNegativeInt(value.accepted) ||
    !isNonNegativeInt(value.quarantined) ||
    !isNonNegativeInt(value.rejected) ||
    !Array.isArray(value.issues) ||
    !value.issues.every(isQualityIssue)
  ) {
    return false;
  }
  return isConsistentVerdict(value.verdict, value.quarantined, value.rejected);
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (parsePositionSnapshot과 같은 관용 — 봉투 도입 여부를 이 파서가 단정하지 않는다). */
function unwrapEnvelope(raw: unknown): unknown {
  if (isRecord(raw) && "data" in raw) return raw.data;
  return raw;
}

function parseSchemaTagged<T>(
  raw: unknown,
  isBody: (value: Record<string, unknown>) => boolean,
): { kind: "ok"; value: T } | { kind: "unsupported_schema_version"; received: unknown } | { kind: "invalid" } {
  const candidate = unwrapEnvelope(raw);
  if (!isRecord(candidate)) return { kind: "invalid" };
  if (candidate.schema_version !== "v1") {
    return { kind: "unsupported_schema_version", received: candidate.schema_version };
  }
  if (!isBody(candidate)) return { kind: "invalid" };
  return { kind: "ok", value: candidate as unknown as T };
}

export function parseCandleSeries(raw: unknown): ParsedCandleSeries {
  return parseSchemaTagged(raw, isCandleSeriesBody);
}

export function parseQualityVerdict(raw: unknown): ParsedQualityVerdict {
  return parseSchemaTagged(raw, isQualityVerdictBody);
}
