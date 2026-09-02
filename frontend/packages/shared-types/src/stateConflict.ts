// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy: 409는 두 갈래로
// 갈린다 — STATE_CONCURRENCY_CONFLICT(낙관적 잠금 충돌, 재조회 후 1회 재시도하면 대개
// 해소됨)와 STATE_INVALID_TRANSITION(현재 상태에서 애초에 불가능한 전이, 재시도 무의미).
// 지금까지는 두 코드가 같은 409 실패로 뭉개졌다 — 이 파일은 409 응답을 그 갈래로
// 나누는 순수 함수만 담당한다(재시도 동작은 useConflictRetry 몫).
//
// INTEGRITY_IDEMPOTENCY_CONFLICT도 409지만 "재시도할지"가 아니라 "Idempotency-Key를
// 어떻게 할지" 관점이라 별도 분류가 필요하다 — 그 판정 로직은 이미
// classifyIdempotencyFailure(idempotencyFailure.ts, task-383)가 담당하므로 여기서는
// "idempotency" 갈래로만 태깅하고 실제 처리는 그쪽에 위임한다(중복 구현 금지).
//
// forbidden.ts/accountLockout.ts와 동일하게 이 패키지는 api-client에 의존하지
// 않으므로(순환 의존 방지) ApiError 클래스 대신 덕타이핑으로
// { statusCode, errorCode } 모양만 검사한다.

export const STATE_CONFLICT_STATUS_CODE = 409;

export type StateConflictKind = "refetch_retry" | "invalid_transition" | "idempotency";

export interface StateConflictLike {
  statusCode?: number | null;
  errorCode?: string | null;
}

function isStateConflictLike(err: unknown): err is StateConflictLike {
  return typeof err === "object" && err !== null && "statusCode" in err;
}

// status가 409가 아니면 null(409 분기와 무관한 에러 — throw하지 않는다). 409면 반드시
// 세 갈래 중 하나로 수렴한다 — 107 §3.2 미지 코드 fallback 원칙에 따라, 아는 코드가
// 아니어도(STATE_INVALID_TRANSITION·미래에 추가될 다른 409 코드 포함) throw 없이
// "invalid_transition"으로 폴백한다(재시도 금지 쪽 안전).
export function classifyStateConflict(err: unknown): StateConflictKind | null {
  if (!isStateConflictLike(err) || err.statusCode !== STATE_CONFLICT_STATUS_CODE) return null;

  const errorCode = err.errorCode;
  if (errorCode === "STATE_CONCURRENCY_CONFLICT") return "refetch_retry";
  if (errorCode === "INTEGRITY_IDEMPOTENCY_CONFLICT") return "idempotency";
  return "invalid_transition";
}
