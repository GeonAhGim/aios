// task-2337(FE-OPS-3): apiRoutes.ts가 P6 300줄 상한에 이미 닿아 있어(task-2098) 새
// 라우트를 더 담을 여유가 없다 — 이 파일은 그 등록 표의 연장(계속 apiRoutes.ts의
// API_ROUTES에 스프레드로 합쳐진다, 별도 표가 아니다)이다. defineApiRoutes()의 중복
// legacyPath 검사는 병합된 최종 객체에서 한 번만 돌아가므로 여기서는 route()만 쓴다.
//
// reconciliation.py/evidence.py 원문 확인 — 둘 다 `-> ApiResponse[...]`+`ok(...)`라
// envelope=true(contracts/openapi/v1.json에서 ApiResponse_ReconciliationStateListResponse_/
// ApiResponse_ReconciliationStateView_/ApiResponse_AuditTimelinePage_/
// ApiResponse_dict_str__bool__ 참조를 node로 직접 확인 — STALE_SNAPSHOT_WHITELIST 대상
// 아님). v1Path는 mandates.*와 동일 사유로 mount_v1(PLT-16) 미도달이라 null. POST
// "/runs"(대사 실행)는 decision상 이 리프의 UI 범위 밖이라 등록하지 않는다(사람이
// EntitySnapshot을 입력해 만드는 화면이 없다 — UNREGISTERED_ROUTE_WHITELIST에 남겨둠).
import { route } from "./apiRouteTypes";

// 명시적 Record<string, ApiRouteDefinition> 타입 주석을 주지 않는다 — 그러면 spread된
// apiRoutes.ts의 API_ROUTES 전체가 인덱스 시그니처로 넓어져 ApiRouteName(keyof typeof
// API_ROUTES)이 문자열 리터럴 유니온이 아니라 그냥 string이 돼 타입 안전성이 사라진다.
// 주석 없이 두면 각 키가 리터럴로 추론된다.
export const FOUNDATION_OPS_ROUTES = {
  "reconciliation.list": route("/v1/foundation/reconciliation", true, null, true),
  "reconciliation.resolve": route("/v1/foundation/reconciliation/:targetRef:resolve", true, null, true),
  "evidence.timeline": route("/v1/foundation/evidence/timeline", true, null, true),
  "evidence.chainVerify": route("/v1/foundation/evidence/chain:verify", true, null, true),
};
