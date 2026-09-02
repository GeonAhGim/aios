// L4_platform_observability_tenancy_api_v1.0.md §3.3 에러 taxonomy 중 거래소·의존성
// 5xx 4개 코드만 다루는 좁은 분류기: EXCHANGE_UNAVAILABLE·DEPENDENCY_NOT_READY(503)는
// 재시도 가능, EXCHANGE_FATAL(502)·INTERNAL_ERROR(500)는 재시도 불가다. 재시도 가능
// 여부 자체의 일반 판정은 이미 classifyRetry(retryable.ts, task-365)가 하므로 그
// 결과(afterSec 계산 포함)를 그대로 재사용하고, 이 모듈은 "이 4개 코드에 한해
// retryable/fatal로 나눈다"는 도메인 분기만 얹는다 — 재구현 금지.
//
// retryable.ts와 동일한 이유로(이 패키지는 api-client에 의존하지 않는다 — 순환 의존
// 방지) ApiError 클래스 대신 덕타이핑으로 { errorCode, retryAfterSec, traceId } 모양만
// 검사한다.

import { classifyRetry } from "./retryable";

export type ServerErrorClassification =
  | { kind: "retryable"; afterSec?: number }
  | { kind: "fatal"; traceId?: string }
  | { kind: "not_applicable" };

export interface ServerErrorLike {
  errorCode?: string | null;
  retryAfterSec?: number | null;
  traceId?: string | null;
}

const RETRYABLE_SERVER_ERROR_CODES: ReadonlySet<string> = new Set([
  "EXCHANGE_UNAVAILABLE",
  "DEPENDENCY_NOT_READY",
]);

const FATAL_SERVER_ERROR_CODES: ReadonlySet<string> = new Set(["EXCHANGE_FATAL", "INTERNAL_ERROR"]);

function isServerErrorLike(err: unknown): err is ServerErrorLike {
  return typeof err === "object" && err !== null && "errorCode" in err;
}

// 표에 없는 나머지 코드(AUTH_*·VALIDATION_* 등)는 이 분류기의 대상이 아니므로
// "not_applicable"로 수렴한다 — 호출부가 이 4개 코드 밖의 에러를 임의로 재시도
// 가능/불가능으로 짐작하지 않도록 명시적으로 구분한다.
export function classifyServerError(err: unknown): ServerErrorClassification {
  if (!isServerErrorLike(err) || !err.errorCode) return { kind: "not_applicable" };

  if (RETRYABLE_SERVER_ERROR_CODES.has(err.errorCode)) {
    const retry = classifyRetry(err);
    return { kind: "retryable", afterSec: retry.kind === "backoff" ? retry.afterSec : undefined };
  }

  if (FATAL_SERVER_ERROR_CODES.has(err.errorCode)) {
    return { kind: "fatal", traceId: err.traceId ?? undefined };
  }

  return { kind: "not_applicable" };
}
