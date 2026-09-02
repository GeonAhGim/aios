// L4_platform_observability_tenancy_api_v1.0.md §3.3 에러 taxonomy 표 1:1 대응.
// 신규 error_code가 서버에 추가돼도 클라이언트가 깨지면 안 되므로(107 §3.2 계약,
// ApiError.error_code는 enum이 아니라 str) 이 파일은 두 단계로 매핑한다:
// 1) 정확히 아는 코드는 EXACT_MESSAGES, 2) 모르는 코드는 접두(prefix) 계열로
// 대략적인 안내를 주고, 그마저 없으면 DEFAULT_API_ERROR_MESSAGE로 수렴한다.

export type ApiErrorCode =
  | "VALIDATION_INVALID_FIELD"
  | "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"
  | "VALIDATION_DISCLOSURE_RETIRED"
  | "AUTH_REQUIRED"
  | "AUTH_INVALID_CREDENTIALS"
  | "AUTH_TOKEN_EXPIRED"
  | "AUTH_TOKEN_INVALID"
  | "AUTH_SESSION_REVOKED"
  | "AUTH_ACCOUNT_LOCKED"
  | "AUTH_MFA_REQUIRED"
  | "AUTH_MFA_INVALID"
  | "AUTH_TENANT_MISMATCH"
  | "AUTHZ_FORBIDDEN"
  | "AUTHZ_ZONE_VIOLATION"
  | "POLICY_LIVE_BLOCKED"
  | "RESOURCE_NOT_FOUND"
  | "STATE_CONCURRENCY_CONFLICT"
  | "STATE_INVALID_TRANSITION"
  | "INTEGRITY_IDEMPOTENCY_CONFLICT"
  | "RATE_LIMIT_EXCEEDED"
  | "EXCHANGE_UNAVAILABLE"
  | "EXCHANGE_FATAL"
  | "DEPENDENCY_NOT_READY"
  | "INTERNAL_ERROR";

export const DEFAULT_API_ERROR_MESSAGE =
  "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.";

// spec §9 PLT-25: 429 응답의 error_code. ErrorMessage가 카운트다운 UI를 보여줄지
// 판단하는 단일 출처 — 문자열을 직접 비교하지 않도록 export한다.
export const RATE_LIMIT_ERROR_CODE: ApiErrorCode = "RATE_LIMIT_EXCEEDED";

const EXACT_MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  VALIDATION_INVALID_FIELD: "입력값을 확인해주세요.",
  VALIDATION_IDEMPOTENCY_KEY_REQUIRED: "요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요.",
  VALIDATION_DISCLOSURE_RETIRED: "내용이 갱신되었습니다. 최신 내용을 다시 불러와주세요.",
  AUTH_REQUIRED: "로그인이 필요합니다.",
  AUTH_INVALID_CREDENTIALS: "이메일 또는 비밀번호가 올바르지 않습니다.",
  AUTH_TOKEN_EXPIRED: "세션이 만료되었습니다. 다시 로그인해주세요.",
  AUTH_TOKEN_INVALID: "인증 정보가 유효하지 않습니다. 다시 로그인해주세요.",
  AUTH_SESSION_REVOKED: "다른 곳에서 로그아웃되어 세션이 종료되었습니다. 다시 로그인해주세요.",
  AUTH_ACCOUNT_LOCKED: "계정이 잠겼습니다. 잠시 후 다시 시도해주세요.",
  AUTH_MFA_REQUIRED: "추가 인증이 필요합니다.",
  AUTH_MFA_INVALID: "인증 코드가 올바르지 않습니다. 다시 시도해주세요.",
  AUTH_TENANT_MISMATCH: "이 리소스에 접근할 권한이 없습니다.",
  AUTHZ_FORBIDDEN: "이 작업을 수행할 권한이 없습니다.",
  AUTHZ_ZONE_VIOLATION: "허용되지 않은 영역에 대한 요청입니다.",
  POLICY_LIVE_BLOCKED: "실거래 모드에서는 허용되지 않는 작업입니다.",
  RESOURCE_NOT_FOUND: "요청한 항목을 찾을 수 없습니다.",
  STATE_CONCURRENCY_CONFLICT: "다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요.",
  STATE_INVALID_TRANSITION: "현재 상태에서는 수행할 수 없는 작업입니다.",
  INTEGRITY_IDEMPOTENCY_CONFLICT: "이미 처리된 요청입니다. 새로고침 후 다시 시도해주세요.",
  RATE_LIMIT_EXCEEDED: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
  EXCHANGE_UNAVAILABLE: "거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요.",
  EXCHANGE_FATAL: "거래소 자격증명을 확인해주세요.",
  DEPENDENCY_NOT_READY: "서비스가 준비 중입니다. 잠시 후 다시 시도해주세요.",
  INTERNAL_ERROR: "일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.",
};

// 표에 개별 코드로 나열되지 않은 계열(`POLICY_*`/`RISK_*` 등, 도메인별 접두
// 화이트리스트는 error_codes.py가 강제)을 위한 대략적인 안내. 정확한 코드가
// EXACT_MESSAGES에 없을 때만 순서대로 검사한다.
const PREFIX_MESSAGES: Array<[string, string]> = [
  ["POLICY_", "정책상 허용되지 않는 요청입니다."],
  ["RISK_", "위험 관리 정책에 의해 거부된 요청입니다."],
  ["VALIDATION_", "입력값을 확인해주세요."],
  ["AUTHZ_", "이 작업을 수행할 권한이 없습니다."],
  ["AUTH_", "인증이 필요합니다. 다시 로그인해주세요."],
  ["STATE_", "현재 상태에서는 수행할 수 없는 작업입니다."],
  ["INTEGRITY_", "요청 처리 중 충돌이 발생했습니다."],
  ["RATE_LIMIT_", "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."],
  ["EXCHANGE_", "거래소 연결에 문제가 발생했습니다."],
  ["DEPENDENCY_", "서비스가 준비 중입니다. 잠시 후 다시 시도해주세요."],
];

// error_code가 알려진 값이면 고정 한국어 메시지를, 모르는 값이면 접두 계열
// 안내를, 그마저 없으면 서버가 준 message나 기본 문구를 반환한다.
export function getApiErrorMessage(
  errorCode?: string | null,
  fallbackMessage?: string | null,
): string {
  if (errorCode) {
    const exact = EXACT_MESSAGES[errorCode as ApiErrorCode];
    if (exact) return exact;
    const prefixHit = PREFIX_MESSAGES.find(([prefix]) => errorCode.startsWith(prefix));
    if (prefixHit) return prefixHit[1];
  }
  return fallbackMessage || DEFAULT_API_ERROR_MESSAGE;
}

// task-354: 세션이 실제로 무효화됐다고 볼 수 있는 코드만 전역 로그아웃 대상이다.
// AUTH_TENANT_MISMATCH는 접두는 AUTH_지만 "유효한 세션으로 잘못된 테넌트
// 리소스에 접근"한 것뿐이라 로그아웃하면 안 된다(에러 표시만, negative test로
// 고정) — 그래서 접두 매칭에서 명시적으로 제외한다.
export function isSessionExpiredErrorCode(errorCode?: string | null): boolean {
  return !!errorCode && errorCode.startsWith("AUTH_") && errorCode !== "AUTH_TENANT_MISMATCH";
}
