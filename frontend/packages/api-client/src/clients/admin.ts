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
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-18 관리자 도구 / FD-10.1 승인요청. task-112(28cf21b)로 admin.py 라우터
// 전체가 ApiResponse 봉투를 적용해 requestEnvelope 계열을 쓴다.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// task-1159: 조회(GET) 6건을 봉투 분기가 apiPaths.ts 레지스트리(envelope 값)
// 단일 출처가 되도록 옮겼다 — 치환·쿼리가 없는 getVerificationQueue는
// requestByRoute로, 나머지(쿼리 4건 + :disputeId 치환 1건)는 경로 조립은 그대로
// 두고 resolveEnvelope(route)로 request/requestEnvelope 분기만 이관했다(둘 다
// requestByRoute가 경로 치환·쿼리를 지원하지 않아서다). 분기 결과는 전부 동일
// (admin.* 전 라우트 envelope=true → requestEnvelope 경로 유지).
export function withAdmin<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getVerificationQueue(): Promise<QueuedListing[]> {
      return this.requestByRoute("admin.verificationQueue");
    }

    async listAdminDisputes(disputeStatus?: string): Promise<DisputeSummary[]> {
      const path = this.withQuery(resolvePath("admin.disputes.list"), { dispute_status: disputeStatus });
      return resolveEnvelope("admin.disputes.list") ? this.requestEnvelope(path) : this.request(path);
    }

    async getAdminDispute(disputeId: number): Promise<DisputeDetail> {
      const path = resolvePath("admin.disputes.get").replace(":disputeId", String(disputeId));
      return resolveEnvelope("admin.disputes.get") ? this.requestEnvelope(path) : this.request(path);
    }

    async resolveDispute(
      disputeId: number,
      body: DisputeResolveRequest,
    ): Promise<DisputeResolutionResult> {
      return this.postEnvelope(
        resolvePath("admin.disputes.resolve").replace(":disputeId", String(disputeId)),
        body,
      );
    }

    async listAdminUsers(emailSearch?: string): Promise<UserSummary[]> {
      const path = this.withQuery(resolvePath("admin.users.list"), { email_search: emailSearch });
      return resolveEnvelope("admin.users.list") ? this.requestEnvelope(path) : this.request(path);
    }

    async changeUserStatus(userId: string, status: string): Promise<UserStatusChangeResult> {
      return this.patchEnvelope(resolvePath("admin.users.status").replace(":userId", userId), { status });
    }

    async suspendSeller(
      userId: string,
      body: SuspendSellerRequest,
    ): Promise<SellerSuspensionResult> {
      return this.postEnvelope(resolvePath("admin.users.suspendSeller").replace(":userId", userId), body);
    }

    async listPendingTopups(page = 1, pageSize = 20): Promise<WalletTopupPage> {
      const path = this.withQuery(resolvePath("admin.wallet.topupsPending"), { page, page_size: pageSize });
      return resolveEnvelope("admin.wallet.topupsPending") ? this.requestEnvelope(path) : this.request(path);
    }

    async confirmTopup(
      topupId: number,
      idempotencyKey?: string,
    ): Promise<WalletTopupConfirmResult> {
      return this.postEnvelopeIdempotent(
        resolvePath("admin.wallet.topupConfirm").replace(":topupId", String(topupId)),
        undefined,
        idempotencyKey,
      );
    }

    async createPlatformListing(body: PlatformListingCreateRequest): Promise<ListingResponse> {
      return this.postEnvelope(resolvePath("admin.marketplace.platformListings"), body);
    }

    async approveRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(
        resolvePath("admin.approvalRequests.approve").replace(":requestId", String(requestId)),
      );
    }

    async rejectRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(
        resolvePath("admin.approvalRequests.reject").replace(":requestId", String(requestId)),
      );
    }

    async listPendingApprovalRequests(scope?: "USER" | "PLATFORM"): Promise<ApprovalRequest[]> {
      const path = this.withQuery(resolvePath("admin.approvalRequests.pending"), { scope });
      return resolveEnvelope("admin.approvalRequests.pending") ? this.requestEnvelope(path) : this.request(path);
    }
  };
}
