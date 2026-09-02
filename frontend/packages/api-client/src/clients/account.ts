import type {
  ApprovalRequest,
  ApprovalSettingsRequest,
  ApprovalSettingsResponse,
  DeletionRequest,
  DeletionResponse,
  RiskProfileHistoryEntry,
  RiskProfileResponse,
  SuitabilityAnswers,
  WhitelistEntryRequest,
  WhitelistEntryResponse,
} from "@aios/shared-types";
import { ApiError, type AnyConstructor } from "../http";

// FD-15.1/15.2 적합성평가 — suitability.py 라우터는 봉투 미적용, 기존 경로 유지.
// FD-11.3/11.5/11.6/10.1 계정 설정 — users.py 라우터는 task-112(28cf21b)로
// 봉투가 적용돼 requestEnvelope 계열을 쓴다.
export function withAccount<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async submitRiskAssessment(body: SuitabilityAnswers): Promise<RiskProfileResponse> {
      return this.post("/users/me/risk-assessment", body);
    }

    async getRiskProfile(): Promise<RiskProfileResponse | null> {
      try {
        return await this.request("/users/me/risk-profile");
      } catch (e) {
        if (e instanceof ApiError && e.statusCode === 404) return null;
        throw e;
      }
    }

    async getRiskProfileHistory(): Promise<RiskProfileHistoryEntry[]> {
      return this.request("/users/me/risk-profile/history");
    }

    async getApprovalSettings(): Promise<ApprovalSettingsResponse> {
      return this.requestEnvelope("/users/me/approval-settings");
    }

    async updateApprovalSettings(body: ApprovalSettingsRequest): Promise<ApprovalSettingsResponse> {
      return this.putEnvelope("/users/me/approval-settings", body);
    }

    async listWhitelistEntries(): Promise<WhitelistEntryResponse[]> {
      return this.requestEnvelope("/users/me/withdrawal-whitelist");
    }

    async registerWhitelistEntry(body: WhitelistEntryRequest): Promise<WhitelistEntryResponse> {
      return this.postEnvelope("/users/me/withdrawal-whitelist", body);
    }

    async requestAccountDeletion(body: DeletionRequest): Promise<DeletionResponse> {
      return this.postEnvelope("/users/me/delete", body);
    }

    async listMyApprovalRequests(): Promise<ApprovalRequest[]> {
      return this.requestEnvelope("/users/me/approval-requests");
    }

    async approveMyRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(`/users/me/approval-requests/${requestId}/approve`);
    }

    async rejectMyRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(`/users/me/approval-requests/${requestId}/reject`);
    }
  };
}
