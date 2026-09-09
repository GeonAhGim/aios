// src/foundation/reconciliation/contracts/v1.py 1:1 대응. task-2337(FE-OPS-3):
// 대사(reconciliation) 상태 조회·해소(resolve)만 다룬다 — 대사 실행(POST /runs)은
// 다른 bounded context가 EntitySnapshot을 만들어 트리거하는 내부 배치 흐름이라
// 사람이 값을 입력해 만드는 UI가 없다(§C decision, 화면은 서버 판정을 표시만 한다).

export type ReconciliationClassification =
  | "HEALTHY"
  | "PENDING"
  | "MINOR_DIFFERENCE"
  | "MATERIAL_MISMATCH"
  | "PROVIDER_UNAVAILABLE"
  | "INVESTIGATING"
  | "RESOLVED";

export interface ReconciliationStateView {
  targetRef: string;
  targetType: string;
  aggregateStatus: ReconciliationClassification;
  lastHealthyAt: string | null;
  lastCheckedAt: string;
  blockingReason: string | null;
  revision: number;
  schemaVersion: string;
}

export interface ReconciliationStateListResponse {
  states: ReconciliationStateView[];
  asOf: string;
}

// resolve_reconciliation.py:23-29 — MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE/
// INVESTIGATING만 해소 대상이다. 판정은 서버가 하므로(NotResolvableError, 409)
// 여기서는 화면이 액션 버튼 노출 여부를 결정할 때만 재사용한다.
export const RESOLVABLE_RECONCILIATION_STATUSES: ReadonlySet<ReconciliationClassification> = new Set([
  "MATERIAL_MISMATCH",
  "PROVIDER_UNAVAILABLE",
  "INVESTIGATING",
]);

export interface ResolveReconciliationRequest {
  reason: string;
}
