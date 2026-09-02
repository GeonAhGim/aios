// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy: 400은 네 갈래로
// 갈린다 — VALIDATION_INVALID_FIELD(필드별 오류, details.fields[] 폼 인라인 표시는
// task-364 extractFieldErrors 몫), VALIDATION_IDEMPOTENCY_KEY_REQUIRED(Idempotency-Key
// 헤더 누락, 클라이언트 결함이라 재시도 무의미 — 새로고침 안내), VALIDATION_DISCLOSURE_RETIRED
// (공시 revision이 폐기됨, 최신 재조회 필요), AUTH_MFA_INVALID(MFA 코드 오류, 재입력 유도).
// 지금까지 ErrorMessage는 이 네 갈래를 전부 같은 배너 문구로 뭉갰다 — 이 파일은 400
// 응답을 그 갈래로 나누는 순수 함수만 담당한다(표시는 BadRequestNotice 몫).
//
// forbidden.ts/stateConflict.ts와 동일하게 이 패키지는 api-client에 의존하지
// 않으므로(순환 의존 방지) ApiError 클래스 대신 덕타이핑으로
// { statusCode, errorCode } 모양만 검사한다.

export const BAD_REQUEST_STATUS_CODE = 400;

export type BadRequestKind =
  | "field"
  | "idempotency_key_required"
  | "disclosure_retired"
  | "mfa_invalid"
  | "unknown";

export interface BadRequestLike {
  statusCode?: number | null;
  errorCode?: string | null;
}

function isBadRequestLike(err: unknown): err is BadRequestLike {
  return typeof err === "object" && err !== null && "statusCode" in err;
}

// status가 400이 아니면 null(400 분기와 무관한 에러 — throw하지 않는다). 400이면
// 반드시 다섯 갈래 중 하나로 수렴한다 — 107 §3.2 미지 코드 fallback 원칙에 따라, 아는
// 코드가 아니어도(미래에 추가될 다른 400 코드 포함) throw 없이 "unknown"으로 폴백한다.
export function classifyBadRequest(err: unknown): BadRequestKind | null {
  if (!isBadRequestLike(err) || err.statusCode !== BAD_REQUEST_STATUS_CODE) return null;

  const errorCode = err.errorCode;
  if (errorCode === "VALIDATION_INVALID_FIELD") return "field";
  if (errorCode === "VALIDATION_IDEMPOTENCY_KEY_REQUIRED") return "idempotency_key_required";
  if (errorCode === "VALIDATION_DISCLOSURE_RETIRED") return "disclosure_retired";
  if (errorCode === "AUTH_MFA_INVALID") return "mfa_invalid";
  return "unknown";
}
