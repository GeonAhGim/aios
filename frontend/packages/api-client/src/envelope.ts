// L4 platform spec §3.3 (ApiResponse/ApiError 봉투) 파서.
// 봉투가 아직 도입되지 않은 엔드포인트는 client.ts가 body를 그대로 반환하므로
// 건드리지 않는다 — 이 모듈은 봉투가 도입된 엔드포인트에서만 쓴다.

export interface ApiResponsePageMeta {
  total: number | null;
  page: number | null;
  size: number;
  next_cursor: string | null;
}

export interface ApiResponseMeta {
  trace_id: string;
  as_of: string;
  page: ApiResponsePageMeta | null;
}

export interface ApiErrorBody {
  error_code: string;
  message: string;
  details: Record<string, unknown>;
  trace_id: string;
  retry_after_seconds: number | null;
}

export type EnvelopeResult<T> =
  | { ok: true; data: T; meta: ApiResponseMeta }
  | { ok: false; error: ApiErrorBody };

export class EnvelopeFormatError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function unwrap<T>(body: unknown): EnvelopeResult<T> {
  if (!isRecord(body)) {
    throw new EnvelopeFormatError("ApiResponse 봉투 형식이 아닙니다.");
  }
  if ("error_code" in body) {
    return { ok: false, error: body as unknown as ApiErrorBody };
  }
  if ("data" in body && "meta" in body) {
    return { ok: true, data: body.data as T, meta: body.meta as ApiResponseMeta };
  }
  throw new EnvelopeFormatError("ApiResponse 봉투 형식이 아닙니다.");
}

// spec §9 PLT-05/13: 응답 헤더 X-Trace-Id와 에러 봉투 trace_id 중 존재하는 값을
// 지원코드로 노출한다. 봉투 쪽이 우선이고(§3.3 계약값), 봉투가 없는 레거시
// 에러 응답은 헤더만 남는다. 둘 다 없으면 undefined — throw 금지.
export function resolveTraceId(
  envelopeTraceId: string | null | undefined,
  headerTraceId: string | null | undefined,
): string | undefined {
  return envelopeTraceId || headerTraceId || undefined;
}

// spec §9 PLT-25: 429 RATE_LIMIT_EXCEEDED 재시도 대기 시간(초). 봉투
// error.retry_after_seconds가 있으면 그 값을 쓰고(§2.3 계약값), 없는 응답은
// `Retry-After` 헤더로 폴백한다. 둘 다 없으면 undefined — 임의 기본값을 두지
// 않는다(호출부가 "재시도 버튼 즉시 활성" 등으로 스스로 해석해야 한다).
export function resolveRetryAfterSec(
  envelopeRetryAfterSec: number | null | undefined,
  headerRetryAfter: string | null | undefined,
): number | undefined {
  if (typeof envelopeRetryAfterSec === "number") return envelopeRetryAfterSec;
  if (headerRetryAfter) {
    const parsed = Number(headerRetryAfter);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  return undefined;
}

// spec §3.3 ApiResponse.meta.as_of — 화면에 "데이터가 언제 기준인지"를 보여주기
// 위한 순수 판정 함수. 파싱 실패/미래 시각/누락을 fresh로 침묵 처리하면 실제로는
// 신선도를 알 수 없는 데이터가 "최신"으로 보이게 되므로, 세 경우 모두 isStale을
// null(판정 불가)로 명시하고 kind로 원인을 구분해서 반환한다.
export type FreshnessKind = "ok" | "missing" | "invalid" | "future";

export interface FreshnessOk {
  kind: "ok";
  asOfDate: Date;
  ageSec: number;
  isStale: boolean;
}

export interface FreshnessFuture {
  kind: "future";
  asOfDate: Date;
  ageSec: number;
  isStale: null;
}

export interface FreshnessUnavailable {
  kind: "missing" | "invalid";
  asOfDate: null;
  ageSec: null;
  isStale: null;
}

export type Freshness = FreshnessOk | FreshnessFuture | FreshnessUnavailable;

export interface DeriveFreshnessOptions {
  staleAfterSec: number;
}

export function deriveFreshness(
  asOf: string | null | undefined,
  now: Date,
  { staleAfterSec }: DeriveFreshnessOptions,
): Freshness {
  if (!asOf) {
    return { kind: "missing", asOfDate: null, ageSec: null, isStale: null };
  }

  const asOfDate = new Date(asOf);
  if (Number.isNaN(asOfDate.getTime())) {
    return { kind: "invalid", asOfDate: null, ageSec: null, isStale: null };
  }

  const ageSec = (now.getTime() - asOfDate.getTime()) / 1000;
  if (ageSec < 0) {
    return { kind: "future", asOfDate, ageSec, isStale: null };
  }

  return { kind: "ok", asOfDate, ageSec, isStale: ageSec >= staleAfterSec };
}
