// L4_platform_observability_tenancy_api_v1.0.md §3.5 테넌트 계약 + §9 PLT-29.
// grant/suspend/revoke_membership이 던지는 실패를 화면이 바로 쓸 수 있는 "문구 키" 갈래로
// 좁히는 순수 함수. §3.3 taxonomy 전체는 이미 task-483 routeApiError(errorRouting.ts)가
// 11개 kind로 분류하므로, 이 파일은 그 결과 위에 멤버십 도메인 실패(last-owner 거부·
// cross-tenant·이미 폐기·자기 자신 거부)만 얹는다 — 새 403/404/409 분류기를 만들지
// 않는다(이 leaf의 decision, routeApiError 재사용).
//
// last-owner 거부(§4 I4 STATE_LAST_OWNER)의 wire 표현이 문서 두 곳에서 어긋난다: 이
// task의 note는 "403 POLICY_*"라 적었지만, §3.3 에러표(309행대)는 STATE_LAST_OWNER를
// STATE_INVALID_TRANSITION(409)에 매핑된 "기존 예외"로 나열한다. PLT-29 라우터가 아직
// 없어(서버 미구현) 실물로 확인할 수 없으므로, 이 분류기는 두 표현(403 POLICY_*/RISK_*와
// 409 STATE_INVALID_TRANSITION) 모두를 last_owner_denied로 받아들인다(방어적 매핑) —
// 실서버 확인 후 한쪽이 틀렸다고 밝혀지면 그 분기만 지우면 된다.
//
// "자기 자신 revoke" 거부는 §4.1 전이표가 SuspendMembership에만 명시한 guard(actor≠target
// 또는 OWNER≥2)라 RevokeMembership이 어떤 코드로 응답할지 문서에 없다 — 일반
// 403(AUTHZ_FORBIDDEN 등, POLICY_*/RISK_*·AUTH_TENANT_MISMATCH가 아닌 나머지)으로
// 응답한다고 가정해 action_forbidden으로 분류한다.

import { routeApiError } from "./errorRouting";

export type MembershipMutationErrorReason =
  | "last_owner_denied"
  | "cross_tenant_denied"
  | "action_forbidden"
  | "already_revoked"
  | "unknown_denied";

// ApiError 하나당 정확히 한 reason으로 수렴한다 — 호출부가 routeApiError의 kind를 직접
// 알 필요 없이 멤버십 화면에 의미 있는 4갈래+unknown만 본다. throw하지 않는다(107 §3.2
// 미지 코드 fallback 원칙과 동일) — err.message는 절대 참조하지 않는다.
export function classifyMembershipError(err: unknown): MembershipMutationErrorReason {
  const routed = routeApiError(err);

  switch (routed.kind) {
    case "policy_denied":
    case "invalid_transition":
      return "last_owner_denied";
    case "tenant_mismatch":
      return "cross_tenant_denied";
    case "forbidden":
      return "action_forbidden";
    case "not_found":
      return "already_revoked";
    default:
      return "unknown_denied";
  }
}

const MEMBERSHIP_ERROR_MESSAGES: Record<MembershipMutationErrorReason, string> = {
  last_owner_denied: "테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다.",
  cross_tenant_denied: "다른 테넌트의 멤버십에는 접근할 수 없습니다.",
  action_forbidden: "이 멤버십에 대해 이 작업을 수행할 권한이 없습니다.",
  already_revoked: "이미 폐기되었거나 존재하지 않는 멤버십입니다.",
  unknown_denied: "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.",
};

// 문구는 이 파일에서 고정한다 — apiError.ts의 EXACT_MESSAGES(24개 error_code 전체)와
// 목적이 다르다(여기는 멤버십 4갈래로 이미 좁혀진 reason). err.message를 그대로 노출하지
// 않기 위한 유일한 통로로 이 함수를 쓴다.
export function describeMembershipError(reason: MembershipMutationErrorReason): string {
  return MEMBERSHIP_ERROR_MESSAGES[reason];
}
