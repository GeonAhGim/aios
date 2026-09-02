import type {
  ApprovalRequest,
  DisputeDetail,
  DisputeResolutionResult,
  DisputeResolveRequest,
  DisputeSummary,
  ListingResponse,
  PlatformListingCreateRequest,
  QueuedListing,
  SellerSuspensionResult,
  SuspendSellerRequest,
  UserStatusChangeResult,
  UserSummary,
  WalletTopupConfirmResult,
  WalletTopupPage,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-18 관리자 도구 / FD-10.1 승인요청. task-112(28cf21b)로 admin.py 라우터
// 전체가 ApiResponse 봉투를 적용해 requestEnvelope 계열을 쓴다.
export function withAdmin<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getVerificationQueue(): Promise<QueuedListing[]> {
      return this.requestEnvelope("/admin/verification-queue");
    }

    async listAdminDisputes(disputeStatus?: string): Promise<DisputeSummary[]> {
      return this.requestEnvelope(this.withQuery("/admin/disputes", { dispute_status: disputeStatus }));
    }

    async getAdminDispute(disputeId: number): Promise<DisputeDetail> {
      return this.requestEnvelope(`/admin/disputes/${disputeId}`);
    }

    async resolveDispute(
      disputeId: number,
      body: DisputeResolveRequest,
    ): Promise<DisputeResolutionResult> {
      return this.postEnvelope(`/admin/disputes/${disputeId}/resolve`, body);
    }

    async listAdminUsers(emailSearch?: string): Promise<UserSummary[]> {
      return this.requestEnvelope(this.withQuery("/admin/users", { email_search: emailSearch }));
    }

    async changeUserStatus(userId: string, status: string): Promise<UserStatusChangeResult> {
      return this.patchEnvelope(`/admin/users/${userId}/status`, { status });
    }

    async suspendSeller(
      userId: string,
      body: SuspendSellerRequest,
    ): Promise<SellerSuspensionResult> {
      return this.postEnvelope(`/admin/users/${userId}/suspend-seller`, body);
    }

    async listPendingTopups(page = 1, pageSize = 20): Promise<WalletTopupPage> {
      return this.requestEnvelope(
        this.withQuery("/admin/wallet/topups/pending", { page, page_size: pageSize }),
      );
    }

    async confirmTopup(
      topupId: number,
      idempotencyKey?: string,
    ): Promise<WalletTopupConfirmResult> {
      return this.postEnvelopeIdempotent(`/admin/wallet/topups/${topupId}/confirm`, undefined, idempotencyKey);
    }

    async createPlatformListing(body: PlatformListingCreateRequest): Promise<ListingResponse> {
      return this.postEnvelope("/admin/marketplace/platform-listings", body);
    }

    async approveRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(`/admin/approval-requests/${requestId}/approve`);
    }

    async rejectRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(`/admin/approval-requests/${requestId}/reject`);
    }

    async listPendingApprovalRequests(scope?: "USER" | "PLATFORM"): Promise<ApprovalRequest[]> {
      return this.requestEnvelope(this.withQuery("/admin/approval-requests/pending", { scope }));
    }
  };
}
