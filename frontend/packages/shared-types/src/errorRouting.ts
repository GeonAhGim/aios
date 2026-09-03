// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy 표 전체를 다루는
// 단일 진입점. 개별 분류기(classifyRetry 등)는 각자 좁은 영역(상태 코드 하나 또는 코드
// 몇 개)만 안다 — 호출부(ErrorMessage 등)가 표의 24개 error_code마다 어느 분류기를 어떤
// 순서로 불러야 하는지 직접 기억해야 했다. 이 파일은 그 조합 순서를 한 곳에 고정한다.
// 판정 로직 자체는 재구현하지 않는다 — 기존 분류기 호출 결과를 그대로 위임하므로,
// 분류기가 바뀌면(예: classifyServerError의 재시도 코드 집합이 바뀌면) 이 파일도 자동으로
// 따라간다.
//
// 우선순위 규칙(§3.3 표에서 한 error_code가 여러 분류기에 동시에 걸리는 경우 — 이 두
// 경우 외에는 error_code 하나당 HTTP status가 정확히 하나라 실제 충돌이 없다):
// 1) classifyIdempotencyFailure가 최우선이다.
//    - INTEGRITY_IDEMPOTENCY_CONFLICT(409)는 classifyStateConflict도 "idempotency"로
//      태깅하지만, stateConflict.ts 자체 주석이 "실제 처리는 classifyIdempotencyFailure에
//      위임한다"고 명시한다.
//    - VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 classifyBadRequest도
//      "idempotency_key_required"로 잡는다.
//    두 코드 모두 "재시도할지"가 아니라 "Idempotency-Key를 어떻게 할지"(새 키 발급 vs 헤더
//    누락, 중복 결제 위험 때문에 자동 재시도 절대 금지)가 실제로 필요한 조치이므로 이
//    갈래가 이긴다 — errorRouting.test.ts의 "우선순위" 블록이 두 분류기가 동시에 해당
//    코드에 응답한다는 사실과 어느 쪽이 이기는지를 함께 고정한다.
// 2) 나머지는 classifyForbidden(403) → classifyStateConflict(409, idempotency 갈래는 위
//    1)에서 이미 소진) → classifyBadRequest(400, idempotency_key_required는 위 1)에서 이미
//    소진) → classifyServerError(502/503/500 중 자신이 아는 4개 코드) →
//    classifyRetry(나머지 — 사실상 RATE_LIMIT_EXCEEDED만 남는다) → isResourceNotFound(404)
//    → deriveLockout(423) → isSessionExpiredErrorCode(나머지 401) 순서로 검사한다.
// 3) 위 어느 것에도 안 걸리면(미지 코드 포함) "unknown"으로 수렴한다 — throw 금지, 107 §3.2
//    미지 코드 fallback 원칙과 동일.

import { classifyBadRequest } from "./badRequest";
import { classifyForbidden } from "./forbidden";
import { classifyIdempotencyFailure } from "./idempotencyFailure";
import { classifyRetry } from "./retryable";
import { classifyServerError } from "./serverError";
import { classifyStateConflict } from "./stateConflict";
import { isResourceNotFound } from "./notFound";
import { extractFieldErrors } from "./fieldErrors";
import { extractReasonCodes } from "./reasonCodes";
import { deriveLockout } from "./accountLockout";
import { isSessionExpiredErrorCode } from "./apiError";

export type RoutedApiError =
  | { kind: "field_errors"; fieldErrors: Record<string, string> }
  | { kind: "idempotency_new_key" }
  | { kind: "idempotency_missing_header" }
  | { kind: "disclosure_retired" }
  | { kind: "mfa_invalid" }
  | { kind: "mfa_required" }
  | { kind: "tenant_mismatch" }
  | { kind: "policy_denied"; reasonCodes: string[] }
  | { kind: "forbidden"; reasonCodes: string[] }
  | { kind: "account_locked"; retryAfterSec: number }
  | { kind: "auth_required" }
  | { kind: "not_found" }
  | { kind: "refetch_retry" }
  | { kind: "invalid_transition" }
  | { kind: "backoff_retry"; afterSec?: number }
  | { kind: "server_fatal"; traceId?: string }
  | { kind: "unknown" };

export type RoutedApiErrorKind = RoutedApiError["kind"];

interface ErrorCodeLike {
  errorCode?: string | null;
}

function isErrorCodeLike(err: unknown): err is ErrorCodeLike {
  return typeof err === "object" && err !== null && "errorCode" in err;
}

// ApiError 하나당 정확히 한 RoutedApiError를 돌려준다 — 호출부가 우선순위나 조합 순서를
// 알 필요 없이 kind 하나로 분기하면 된다. err는 각 하위 분류기와 동일하게 덕타이핑으로
// 검사하므로(이 패키지는 api-client에 의존하지 않는다 — 순환 의존 방지) 어떤 값을 넘겨도
// throw하지 않는다.
export function routeApiError(err: unknown): RoutedApiError {
  const idempotency = classifyIdempotencyFailure(err);
  if (idempotency === "new_key") return { kind: "idempotency_new_key" };
  if (idempotency === "missing_header") return { kind: "idempotency_missing_header" };

  const badRequest = classifyBadRequest(err);
  if (badRequest === "field") return { kind: "field_errors", fieldErrors: extractFieldErrors(err) };
  if (badRequest === "disclosure_retired") return { kind: "disclosure_retired" };
  if (badRequest === "mfa_invalid") return { kind: "mfa_invalid" };

  const forbidden = classifyForbidden(err);
  if (forbidden === "mfa_required") return { kind: "mfa_required" };
  if (forbidden === "tenant_mismatch") return { kind: "tenant_mismatch" };
  if (forbidden === "policy") return { kind: "policy_denied", reasonCodes: extractReasonCodes(err) };
  if (forbidden === "forbidden") return { kind: "forbidden", reasonCodes: extractReasonCodes(err) };

  const stateConflict = classifyStateConflict(err);
  if (stateConflict === "refetch_retry") return { kind: "refetch_retry" };
  if (stateConflict === "invalid_transition") return { kind: "invalid_transition" };

  const serverError = classifyServerError(err);
  if (serverError.kind === "retryable") return { kind: "backoff_retry", afterSec: serverError.afterSec };
  if (serverError.kind === "fatal") return { kind: "server_fatal", traceId: serverError.traceId };

  const retry = classifyRetry(err);
  if (retry.kind === "refetch") return { kind: "refetch_retry" };
  if (retry.kind === "backoff") return { kind: "backoff_retry", afterSec: retry.afterSec };

  if (isResourceNotFound(err)) return { kind: "not_found" };

  const lockout = deriveLockout(err);
  if (lockout.locked) return { kind: "account_locked", retryAfterSec: lockout.retryAfterSec };

  if (isSessionExpiredErrorCode(isErrorCodeLike(err) ? err.errorCode : undefined)) {
    return { kind: "auth_required" };
  }

  return { kind: "unknown" };
}
