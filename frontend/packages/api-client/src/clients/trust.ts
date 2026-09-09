import type { ConsentDecisionView, GrantMembershipBody, TrustMembershipView, TrustStatusView } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2338(FE-OPS-4): trust.py(GET /status, POST /consents/{consent_id}:revoke)와
// trust_memberships.py(POST /memberships, POST /memberships/{subject_id}:suspend,
// POST /memberships/{subject_id}:revoke) 5라우트 클라이언트. 전부 `-> ApiResponse[...]`
// + `ok(...)`로 응답하므로(envelope=true) postEnvelope/requestByRoute만 쓴다
// (mandates.ts/reconciliation.ts와 동일 관용, 새 파서 없음). trust_memberships.py에는
// 목록 조회(GET) 라우트가 없다 — grant/suspend/revoke 각 응답(MembershipResponse)이
// 그 대상 멤버십의 최신 상태를 그대로 돌려주므로, 화면은 그 응답을 진실로 쓴다.
export function withTrust<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getTrustStatus(): Promise<TrustStatusView> {
      return this.requestByRoute("trust.status");
    }

    async revokeConsent(consentId: string): Promise<ConsentDecisionView> {
      const path = resolvePath("trust.consents.revoke").replace(":consentId", consentId);
      return this.postEnvelope(path);
    }

    async grantMembership(body: GrantMembershipBody): Promise<TrustMembershipView> {
      return this.postEnvelope(resolvePath("trust.memberships.grant"), body);
    }

    async suspendMembership(subjectId: string): Promise<TrustMembershipView> {
      const path = resolvePath("trust.memberships.suspend").replace(":subjectId", subjectId);
      return this.postEnvelope(path);
    }

    async revokeMembership(subjectId: string): Promise<TrustMembershipView> {
      const path = resolvePath("trust.memberships.revoke").replace(":subjectId", subjectId);
      return this.postEnvelope(path);
    }
  };
}
