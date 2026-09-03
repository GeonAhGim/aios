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
import { resolvePath } from "../apiPaths";
import { ApiError, type AnyConstructor } from "../http";

// FD-15.1/15.2 적합성평가 — suitability.py 라우터는 봉투 미적용, 기존 경로 유지.
// FD-11.3/11.5/11.6/10.1 계정 설정 — users.py 라우터는 task-112(28cf21b)로
// 봉투가 적용돼 requestEnvelope 계열을 쓴다.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// task-1159: 경로 치환·쿼리가 없는 단순 조회(GET) 5건은 http.ts의 requestByRoute로
// 옮겨 request/requestEnvelope 분기를 apiPaths.ts 레지스트리(envelope 값) 단일
// 출처로 이관했다 — 분기 결과 자체는 바꾸지 않는다(§3.3, PLT-17~21).
export function withAccount<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async submitRiskAssessment(body: SuitabilityAnswers): Promise<RiskProfileResponse> {
      return this.post(resolvePath("account.riskAssessment"), body);
    }

    async getRiskProfile(): Promise<RiskProfileResponse | null> {
      try {
        return await this.requestByRoute("account.riskProfile");
      } catch (e) {
        if (e instanceof ApiError && e.statusCode === 404) return null;
        throw e;
      }
    }

    async getRiskProfileHistory(): Promise<RiskProfileHistoryEntry[]> {
      return this.requestByRoute("account.riskProfileHistory");
    }

    async getApprovalSettings(): Promise<ApprovalSettingsResponse> {
      return this.requestByRoute("account.approvalSettings");
    }

    async updateApprovalSettings(body: ApprovalSettingsRequest): Promise<ApprovalSettingsResponse> {
      return this.putEnvelope(resolvePath("account.approvalSettings"), body);
    }

    async listWhitelistEntries(): Promise<WhitelistEntryResponse[]> {
      return this.requestByRoute("account.whitelist");
    }

    async registerWhitelistEntry(body: WhitelistEntryRequest): Promise<WhitelistEntryResponse> {
      return this.postEnvelope(resolvePath("account.whitelist"), body);
    }

    async requestAccountDeletion(body: DeletionRequest): Promise<DeletionResponse> {
      return this.postEnvelope(resolvePath("account.deletion"), body);
    }

    async listMyApprovalRequests(): Promise<ApprovalRequest[]> {
      return this.requestByRoute("account.approvalRequests.list");
    }

    async approveMyRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(
        resolvePath("account.approvalRequests.approve").replace(":requestId", String(requestId)),
      );
    }

    async rejectMyRequest(requestId: number): Promise<ApprovalRequest> {
      return this.postEnvelope(
        resolvePath("account.approvalRequests.reject").replace(":requestId", String(requestId)),
      );
    }
  };
}
