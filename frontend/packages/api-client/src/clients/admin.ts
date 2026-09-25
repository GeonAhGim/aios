import type {
  ApprovalRequest,
  AuditLogPage,
  DisputeDetail,
  DisputeResolutionResult,
  DisputeResolveRequest,
  DisputeSummary,
  ListingResponse,
  MarkPayoutPaidResult,
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

    // task-4024(FE-OPS-7a): admin.py:84 GET /admin/audit-log · ledger_admin.py:53 POST
    // /admin/ledger/payouts/{batch_id}/paid 둘 다 PLT-35-fix(task-3850)의
    // require_break_glass("tenant_read")를 소비한다(X-Break-Glass-Grant 헤더, UUID) —
    // 그 헤더를 채우는 그랜트 요청/승인 화면은 아직 없어(apiPaths.openapi.test.ts의
    // UNREGISTERED_ROUTE_WHITELIST에 남은 /admin/break-glass/grants* 항목 참고) 이
    // 리프는 호출자가 이미 들고 있는 grant id를 헤더로 그대로 흘려보내기만 한다.
    async listAuditLog(
      breakGlassGrantId: string,
      filters: {
        actionType?: string;
        targetType?: string;
        targetId?: string;
        page?: number;
        pageSize?: number;
      } = {},
    ): Promise<AuditLogPage> {
      const path = this.withQuery(resolvePath("admin.auditLog"), {
        action_type: filters.actionType,
        target_type: filters.targetType,
        target_id: filters.targetId,
        page: filters.page,
        page_size: filters.pageSize,
      });
      const init: RequestInit = { headers: { "X-Break-Glass-Grant": breakGlassGrantId } };
      return resolveEnvelope("admin.auditLog") ? this.requestEnvelope(path, init) : this.request(path, init);
    }

    // apiRoutes.ts의 admin.ledger.payoutsMarkPaid 등록 주석 참조 — mark_payout_paid
    // (LC-15a)는 배치 상태 조건부 UPDATE로 재확정을 막아 spec §9 PLT-15 금전 라우트
    // 표에 없다(postEnvelopeIdempotent 대상이 아니다). http.ts의 postEnvelope에
    // extraHeaders(task-4024)를 얹어 X-Break-Glass-Grant를 싣는다 — resolveDispute·
    // changeUserStatus 등 다른 admin.* POST/PATCH와 동일하게 "항상 봉투"를 postEnvelope
    // 선택으로 표현한다(apiPaths.clientsScan.test.ts §task-1160가 this.requestEnvelope
    // 직접 호출의 하드코딩 분기를 금지한다).
    async markPayoutPaid(
      batchId: string,
      externalRef: string,
      breakGlassGrantId: string,
    ): Promise<MarkPayoutPaidResult> {
      return this.postEnvelope(
        resolvePath("admin.ledger.payoutsMarkPaid").replace(":batchId", batchId),
        { externalRef },
        { "X-Break-Glass-Grant": breakGlassGrantId },
      );
    }
  };
}
