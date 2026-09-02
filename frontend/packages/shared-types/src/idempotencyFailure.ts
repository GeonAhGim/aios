// L4_platform_observability_tenancy_api_v1.0.md §3.3 에러 taxonomy 중
// INTEGRITY_IDEMPOTENCY_CONFLICT(409)/VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)
// 두 코드는 classifyRetry(retryable.ts)가 이미 kind:"none"으로 분류한다(재시도해도
// 같은 요청이면 같은 결과가 반복됨) — 그래서 이 둘은 "재시도할지"가 아니라
// "Idempotency-Key(task-151 IdempotencyKeyManager)를 어떻게 할지" 관점에서 별도로
// 분류한다. §9 PLT-14/15.
//
// retryable.ts와 마찬가지로 api-client에 의존하지 않도록(순환 의존 방지) 덕타이핑으로
// { errorCode } 모양만 검사한다.

export type IdempotencyFailureKind = "new_key" | "missing_header" | "none";

export interface IdempotencyFailureLike {
  errorCode?: string | null;
}

function isIdempotencyFailureLike(err: unknown): err is IdempotencyFailureLike {
  return typeof err === "object" && err !== null && "errorCode" in err;
}

/**
 * INTEGRITY_IDEMPOTENCY_CONFLICT(409) → "new_key": 같은 Idempotency-Key로 이전과
 * 다른 요청 바디가 들어왔다는 뜻이다. 그 키는 오염된 것으로 보고 폐기해야 하며,
 * 중복 결제 위험 때문에 자동 재제출은 절대 하지 않는다 — 사용자가 다시 제출을
 * 눌러야 새 키로 시도한다(useIdempotentSubmit).
 *
 * VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400) → "missing_header": 헤더 자체를 보내지
 * 않았다는 뜻이라 클라이언트 개발 결함이다. 같은 코드로 재시도해도 헤더가 계속
 * 빠진 채 나가므로 항상 같은 결과이고, 자동/수동 재시도 모두 의미가 없다.
 *
 * 그 외 코드(알 수 없는 코드 포함)는 "none"으로 수렴한다.
 */
export function classifyIdempotencyFailure(err: unknown): IdempotencyFailureKind {
  if (!isIdempotencyFailureLike(err) || !err.errorCode) return "none";
  if (err.errorCode === "INTEGRITY_IDEMPOTENCY_CONFLICT") return "new_key";
  if (err.errorCode === "VALIDATION_IDEMPOTENCY_KEY_REQUIRED") return "missing_header";
  return "none";
}
