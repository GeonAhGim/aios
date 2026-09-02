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
