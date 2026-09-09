import { Badge } from "./Badge";

const SUCCESS_STATUSES = new Set([
  "ACTIVE",
  "RUNNING",
  "APPROVED",
  "LISTED",
  "CONFIRMED",
  "SENT",
  "RESOLVED",
  "HEALTHY",
  "SUCCESS",
]);
const DANGER_STATUSES = new Set([
  "SUSPENDED",
  "FAILED",
  "REJECTED",
  "RETIRED",
  "EXPIRED",
  "DELISTED",
  "DELETED",
  // task-2337(FE-OPS-3): 대사(reconciliation) 불일치·증빙 이벤트 결과.
  "MATERIAL_MISMATCH",
  "DENIED",
  "ERROR",
  // task-2338(FE-OPS-4): 신뢰(trust) 동의·멤버십 폐기 상태.
  "REVOKED",
]);
const WARNING_STATUSES = new Set([
  "PAUSED",
  "PENDING_APPROVAL",
  "PENDING_VERIFICATION",
  "PENDING_PAYMENT",
  "OPEN",
  "DRAFT",
  // task-2337(FE-OPS-3): 대사 조사 중/공급자 응답 없음/경미한 차이 상태.
  "PENDING",
  "MINOR_DIFFERENCE",
  "PROVIDER_UNAVAILABLE",
  "INVESTIGATING",
]);

// 여러 도메인(실행 status, 결제 status, 분쟁 status 등)이 같은 3단계
// 의미(정상/대기/문제)를 공유하므로 문자열 값 하나로 톤을 결정한다 —
// 도메인마다 별도 매핑을 만들지 않는다.
export function StatusBadge({ status }: { status: string }) {
  const tone = SUCCESS_STATUSES.has(status)
    ? "success"
    : DANGER_STATUSES.has(status)
      ? "danger"
      : WARNING_STATUSES.has(status)
        ? "warning"
        : "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}
