// L4_platform_observability_tenancy_api_v1.0.md §3.3 AUTH_ACCOUNT_LOCKED(423): "재시도:
// retry_after_seconds 후". retryAfterSec 자체는 계산하지 않고 ApiError.retryAfterSec
// (api-client가 §9 PLT-25 규칙으로 이미 봉투 retry_after_seconds/Retry-After 헤더에서
// 뽑아둔 값, task-339)을 그대로 읽기만 한다 — 파서 재구현 금지.
//
// retryable.ts와 동일하게 이 패키지는 api-client에 의존하지 않으므로(순환 의존 방지)
// ApiError 클래스 대신 덕타이핑으로 { statusCode, errorCode, retryAfterSec } 모양만 검사한다.

export const ACCOUNT_LOCKED_STATUS_CODE = 423;
export const ACCOUNT_LOCKED_ERROR_CODE = "AUTH_ACCOUNT_LOCKED";

// 서버가 retry_after_seconds를 안 보냈거나 비정상 값(<=0, NaN)이면 카운트다운이 0으로
// 즉시 끝나거나 음수가 되는 것을 막기 위해 이 기본값으로 클램프한다.
export const DEFAULT_ACCOUNT_LOCKOUT_SEC = 60;

export interface AccountLockoutLike {
  statusCode?: number | null;
  errorCode?: string | null;
  retryAfterSec?: number | null;
}

export interface AccountLockout {
  locked: boolean;
  retryAfterSec: number;
}

function isAccountLockoutLike(err: unknown): err is AccountLockoutLike {
  return typeof err === "object" && err !== null && "statusCode" in err;
}

function clampRetryAfterSec(raw: number | null | undefined): number {
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw <= 0) {
    return DEFAULT_ACCOUNT_LOCKOUT_SEC;
  }
  return raw;
}

// status 423 && error_code "AUTH_ACCOUNT_LOCKED"인 에러만 locked=true를 반환한다.
// 그 외 에러(401 AUTH_INVALID_CREDENTIALS 등)는 잠금과 무관하므로 locked=false,
// retryAfterSec=0으로 수렴한다.
export function deriveLockout(err: unknown): AccountLockout {
  if (
    !isAccountLockoutLike(err) ||
    err.statusCode !== ACCOUNT_LOCKED_STATUS_CODE ||
    err.errorCode !== ACCOUNT_LOCKED_ERROR_CODE
  ) {
    return { locked: false, retryAfterSec: 0 };
  }
  return { locked: true, retryAfterSec: clampRetryAfterSec(err.retryAfterSec) };
}
