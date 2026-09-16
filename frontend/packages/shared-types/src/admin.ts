// src/api/schemas/admin.py, src/services/verification_queue_service.py,
// src/services/dispute_resolution_service.py, src/services/user_admin_service.py,
// src/services/seller_suspension_service.py, src/core/approval/service.py 1:1 대응.

import type { PayoutBatchState } from "./holdPayoutView";

export interface QueuedListing {
  listingId: number;
  strategyId: string;
  strategyVersion: string;
  sellerUserId: string;
  price: string | null;
  submittedAt: string;
}

export interface DisputeSummary {
  id: number;
  purchaseId: number;
  submittedBy: string;
  reason: string;
  status: string;
  resolutionDecision: string | null;
  resolutionReason: string | null;
  resolvedBy: string | null;
  createdAt: string;
  resolvedAt: string | null;
}

export interface DisputeDetail {
  disputeId: number;
  purchaseId: number;
  submittedBy: string;
  reason: string;
  status: string;
  listingId: number;
  listingStatus: string;
  sellerUserId: string;
  buyerUserId: string;
  createdAt: string;
}

export interface DisputeResolveRequest {
  decision: string;
  reason: string;
}

export interface DisputeResolutionResult {
  disputeId: number;
  listingStatus: string;
  resolvedAt: string;
}

export interface UserSummary {
  userId: string;
  email: string;
  status: string;
  createdAt: string;
}

export interface UserStatusChangeResult {
  userId: string;
  status: string;
  changedAt: string;
}

export interface SuspendSellerRequest {
  reason: string;
}

export interface SellerSuspensionResult {
  userId: string;
  sellerSuspended: boolean;
  suspendedAt: string;
}

// src/services/audit_log_read_service.py::AuditLogEntry/AuditLogPage 1:1 대응
// (task-4024, FE-OPS-7a). GET /admin/audit-log(admin.py:84)는 http.ts의
// requestEnvelope 경로를 거쳐 이미 camelCase로 변환된 값을 돌려주므로 —
// holdPayoutView.ts의 PayoutBatchView(원시 미변환 JSON 파서용)와 달리 snake_case를
// 쓰지 않는다.
export interface AuditLogEntry {
  logId: number;
  userId: string | null;
  actorAgent: string;
  actionType: string;
  targetType: string | null;
  targetId: string | null;
  decisionData: Record<string, unknown>;
  verificationChain: Record<string, unknown> | null;
  createdAt: string;
}

export interface AuditLogPage {
  items: AuditLogEntry[];
  total: number;
  page: number;
  pageSize: number;
}

// src/foundation/ledger/application/payouts.py::mark_payout_paid의 응답
// (PayoutBatchView, src/foundation/ledger/contracts/v1.py) 1:1 대응 — 위 AuditLogPage와
// 같은 이유로 camelCase(POST /admin/ledger/payouts/{batch_id}/paid도 requestEnvelope
// 경유). state는 holdPayoutView.ts의 PayoutBatchState를 그대로 재사용해 드리프트를
// 만들지 않는다.
export interface MarkPayoutPaidResult {
  batchId: string;
  sellerUserId: string;
  periodStart: string;
  periodEnd: string;
  amount: string;
  state: PayoutBatchState;
  captureEntryIds: string[];
  releaseEntryId: string | null;
  paidEntryId: string | null;
}

export interface ApprovalRequest {
  id: number;
  scope: "USER" | "PLATFORM";
  userId: string | null;
  triggerSource: string;
  provenance: string | null;
  context: Record<string, unknown>;
  requestedAction: string;
  approvalMode: "SOLO" | "DUAL";
  status: string;
  mandatoryWaitSeconds: number;
  firstApproverId: string | null;
  secondApproverId: string | null;
  createdAt: string;
  expiresAt: string;
  resolvedAt: string | null;
}
