// src/api/schemas/admin.py, src/services/verification_queue_service.py,
// src/services/dispute_resolution_service.py, src/services/user_admin_service.py,
// src/services/seller_suspension_service.py, src/services/payment_confirmation_service.py,
// src/core/approval/service.py 1:1 대응.

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

export interface PendingPayment {
  purchaseId: number;
  buyerUserId: string;
  strategyId: string;
  strategyVersion: string;
  pricePaid: string | null;
  purchasedAt: string;
}

export interface PendingPaymentPage {
  items: PendingPayment[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PaymentConfirmationResult {
  purchaseId: number;
  paymentStatus: string;
  confirmedAt: string | null;
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
