// L4_platform_observability_tenancy_api_v1.0.md §3.3 에러 taxonomy 표의 "재시도" 열을
// 그대로 분류한다. retryAfterSec은 계산하지 않고 ApiError.retryAfterSec(api-client가
// 이미 §9 PLT-25 규칙으로 계산해둔 값)을 그대로 읽기만 한다 — 재구현 금지.
//
// 이 패키지는 api-client에 의존하지 않으므로(순환 의존 방지) ApiError 클래스 대신
// 덕타이핑으로 { errorCode, retryAfterSec } 모양만 검사한다.

export type RetryKind = "none" | "refetch" | "backoff";

export interface RetryClassification {
  kind: RetryKind;
  /** kind === "backoff"일 때만 의미가 있다. 서버가 준 값이 없으면 undefined. */
  afterSec?: number;
}

export interface RetryableErrorLike {
  errorCode?: string | null;
  retryAfterSec?: number | null;
}

// STATE_CONCURRENCY_CONFLICT(409): 낙관적 잠금 충돌 — 최신 상태를 재조회한 뒤 1회
// 재시도하면 대개 해소된다.
const REFETCH_ERROR_CODES: ReadonlySet<string> = new Set(["STATE_CONCURRENCY_CONFLICT"]);

// RATE_LIMIT_EXCEEDED(429)/EXCHANGE_UNAVAILABLE(503)/DEPENDENCY_NOT_READY(503): 일시적
// 과부하·준비 지연 — 지수 백오프로 재시도할 가치가 있다.
const BACKOFF_ERROR_CODES: ReadonlySet<string> = new Set([
  "RATE_LIMIT_EXCEEDED",
  "EXCHANGE_UNAVAILABLE",
  "DEPENDENCY_NOT_READY",
]);

function isRetryableErrorLike(err: unknown): err is RetryableErrorLike {
  return typeof err === "object" && err !== null && "errorCode" in err;
}

// 표에 나열되지 않은 나머지(INTEGRITY_IDEMPOTENCY_CONFLICT·VALIDATION_*·AUTH_*·
// POLICY_*/RISK_*·EXCHANGE_FATAL·INTERNAL_ERROR 등)는 재시도해도 같은 결과가 반복되므로
// kind: "none"으로 수렴한다.
export function classifyRetry(err: unknown): RetryClassification {
  if (!isRetryableErrorLike(err) || !err.errorCode) return { kind: "none" };

  if (REFETCH_ERROR_CODES.has(err.errorCode)) return { kind: "refetch" };

  if (BACKOFF_ERROR_CODES.has(err.errorCode)) {
    return {
      kind: "backoff",
      afterSec: typeof err.retryAfterSec === "number" ? err.retryAfterSec : undefined,
    };
  }

  return { kind: "none" };
}
