// task-801: http.ts(401줄, P6 300줄 규율 초과) 분할 — 이 모듈은 ApiError 빌드와
// GET 재시도 정책(§3.3/§9 PLT-25) 책임만 담당한다. httpHeaders.ts(헤더 조립)·
// httpIdempotent.ts(멱등 계열)와 나란히, http.ts(코어 fetch·봉투 분기)가
// 그대로 재사용한다 — client.ts 분할(task-132, 4aedd6c) 선례대로 동작을 바꾸지
// 않는 이동이다.

import { classifyServerError } from "@aios/shared-types";
import type { ApiErrorBody } from "./envelope";
import { resolveRetryAfterSec, resolveTraceId } from "./envelope";

export class ApiError extends Error {
  statusCode: number;
  traceId?: string;
  errorCode?: string;
  retryAfterSec?: number;
  // spec §3.3: POLICY_*/RISK_* 봉투의 error.details(reason_codes 등) 원본. task-388 QA가
  // 발견한 결함 — 이 필드가 없어서 shared-types/reasonCodes.ts의 extractReasonCodes가
  // 실제 ApiError 인스턴스에서 항상 빈 배열을 반환했다(DenialReasons가 절대 렌더되지 않음).
  details?: Record<string, unknown>;

  constructor(
    statusCode: number,
    message: string,
    traceId?: string,
    errorCode?: string,
    retryAfterSec?: number,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.statusCode = statusCode;
    this.traceId = traceId;
    this.errorCode = errorCode;
    this.retryAfterSec = retryAfterSec;
    this.details = details;
  }

  // reasonCodes.ts(extractReasonCodes)는 순환 의존을 피하려고 ApiError 클래스가 아닌
  // 서버 봉투 그대로의 snake_case(error_code)를 덕타이핑으로 검사한다 — 그 계약을
  // 바꾸지 않고, 여기서 기존 camelCase errorCode의 별칭만 얹어 접근 가능하게 한다.
  get error_code(): string | undefined {
    return this.errorCode;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// spec §3.3: 503(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY)은 표에 "백오프"만
// 명시되고 retry_after_seconds가 없는 경우가 많아 클라이언트가 직접 스케줄을
// 정한다 — 상한 2회(1초, 2초).
const SERVER_ERROR_BACKOFF_SEC = [1, 2] as const;

// withGetRetry가 다음 재시도까지 기다릴 초. undefined면 그대로 throw한다.
// 429는 attempt 0에서만 서버 값으로 1회, 503(classifyServerError가
// "retryable")은 위 스케줄대로 상한 2회, 502/500 등 그 외는 즉시 안내한다.
function nextRetryDelaySec(err: unknown, attempt: number): number | undefined {
  if (!(err instanceof ApiError)) return undefined;
  if (err.statusCode === 429) {
    return attempt === 0 && typeof err.retryAfterSec === "number" ? err.retryAfterSec : undefined;
  }
  if (classifyServerError(err).kind === "retryable" && attempt < SERVER_ERROR_BACKOFF_SEC.length) {
    return SERVER_ERROR_BACKOFF_SEC[attempt];
  }
  return undefined;
}

// spec §9 PLT-25/§3.3: GET 계열만 자동 재시도한다 — POST 등은 멱등키 규약과
// 충돌하므로 절대 재시도하지 않는다. 간격은 nextRetryDelaySec이 결정한다.
export async function withGetRetry<T>(method: string, exec: () => Promise<T>): Promise<T> {
  if (method !== "GET") return exec();

  for (let attempt = 0; ; attempt++) {
    try {
      return await exec();
    } catch (err) {
      const delaySec = nextRetryDelaySec(err, attempt);
      if (delaySec === undefined) throw err;
      await sleep(delaySec * 1000);
    }
  }
}

function extractDetailMessage(body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .join(", ");
    }
  }
  return "요청을 처리할 수 없습니다.";
}

function isEnvelopeError(body: unknown): body is ApiErrorBody {
  return typeof body === "object" && body !== null && "error_code" in body;
}

// 429(RATE_LIMIT_EXCEEDED)는 미들웨어가 봉투 미적용 라우트에도 §2.3 봉투로
// 응답하므로(spec §9 PLT-25), 봉투/레거시 두 형태를 여기서 함께 처리한다.
// 순수 함수 — 401 처리(재로그인 유도 vs refresh 후 재시도)는 호출부인
// handleAuthFailure가 비동기로 담당한다(task-386). export하는 이유: 호출부(테스트)가
// mock 객체가 아니라 이 함수로 만든 진짜 ApiError 인스턴스로 배선을 검증할 수 있게 한다.
export function buildApiError(
  status: number,
  body: unknown,
  traceId: string | undefined,
  retryAfterHeader: string | undefined,
): ApiError {
  const envelopeError = isEnvelopeError(body) ? body : undefined;
  const message = envelopeError ? envelopeError.message : extractDetailMessage(body);
  const retryAfterSec = resolveRetryAfterSec(envelopeError?.retry_after_seconds, retryAfterHeader);
  return new ApiError(
    status,
    message,
    resolveTraceId(envelopeError?.trace_id, traceId),
    envelopeError?.error_code,
    retryAfterSec,
    envelopeError?.details,
  );
}
