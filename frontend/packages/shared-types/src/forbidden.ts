// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy 표: 403은 네 갈래로
// 갈린다 — AUTH_MFA_REQUIRED(step-up 유도), AUTH_TENANT_MISMATCH(테넌트 불일치),
// POLICY_*/RISK_*(정책·위험 거부, 사유는 reasonCodes.ts/DenialReasons가 이미 담당),
// AUTHZ_FORBIDDEN·AUTHZ_ZONE_VIOLATION·그 외(권한 없음). 지금까지 ErrorMessage는 이
// 네 갈래를 전부 같은 배너 문구로 뭉갰다 — 이 파일은 403 응답을 그 갈래로 나누는
// 순수 함수만 담당한다(표시는 ForbiddenNotice 몫).
//
// retryable.ts/accountLockout.ts와 동일하게 이 패키지는 api-client에 의존하지
// 않으므로(순환 의존 방지) ApiError 클래스 대신 덕타이핑으로
// { statusCode, errorCode } 모양만 검사한다.

export const FORBIDDEN_STATUS_CODE = 403;

export type ForbiddenKind = "mfa_required" | "tenant_mismatch" | "policy" | "forbidden";

export interface ForbiddenLike {
  statusCode?: number | null;
  errorCode?: string | null;
}

const POLICY_ERROR_CODE_PREFIXES = ["POLICY_", "RISK_"];

function isForbiddenLike(err: unknown): err is ForbiddenLike {
  return typeof err === "object" && err !== null && "statusCode" in err;
}

// status가 403이 아니면 null(403 분기와 무관한 에러). 403이면 반드시 네 갈래 중
// 하나로 수렴한다 — 107 §3.2 미지 코드 fallback 원칙에 따라, 아는 코드가 아니어도
// (AUTHZ_FORBIDDEN·AUTHZ_ZONE_VIOLATION·미래에 추가될 다른 403 코드 포함) throw 없이
// "forbidden"으로 폴백한다.
export function classifyForbidden(err: unknown): ForbiddenKind | null {
  if (!isForbiddenLike(err) || err.statusCode !== FORBIDDEN_STATUS_CODE) return null;

  const errorCode = err.errorCode;
  if (errorCode === "AUTH_MFA_REQUIRED") return "mfa_required";
  if (errorCode === "AUTH_TENANT_MISMATCH") return "tenant_mismatch";
  if (typeof errorCode === "string" && POLICY_ERROR_CODE_PREFIXES.some((prefix) => errorCode.startsWith(prefix))) {
    return "policy";
  }
  return "forbidden";
}
