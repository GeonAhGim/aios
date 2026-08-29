import { Badge } from "./Badge";

const SUCCESS_STATUSES = new Set([
  "ACTIVE",
  "RUNNING",
  "APPROVED",
  "LISTED",
  "CONFIRMED",
  "SENT",
  "RESOLVED",
]);
const DANGER_STATUSES = new Set([
  "SUSPENDED",
  "FAILED",
  "REJECTED",
  "RETIRED",
  "EXPIRED",
  "DELISTED",
  "DELETED",
]);
const WARNING_STATUSES = new Set([
  "PAUSED",
  "PENDING_APPROVAL",
  "PENDING_VERIFICATION",
  "PENDING_PAYMENT",
  "OPEN",
  "DRAFT",
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
