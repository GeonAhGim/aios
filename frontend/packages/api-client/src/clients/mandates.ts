import type {
  ActivateRevisionRequest,
  MandateRevisionView,
  MandateRuleInput,
  MandateStatusResponse,
  PolicyDecisionView,
  PolicyEvaluationSubject,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2336(FE-OPS-2): 위임장(mandate) status·drafts·amendments·activate(revisions/
// {revision_id}:activate)·pause·resume·policy:evaluate 7라우트 전부를 다루는
// 클라이언트 — src/api/routers/foundation/mandates.py 원문 기준. 전부
// ApiResponse[...] + ok()로 응답하므로(envelope=true) postEnvelope/requestByRoute만
// 쓴다(riskGate.ts와 동일 관용, 새 파서 없음).
export function withMandates<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getMandateStatus(): Promise<MandateStatusResponse> {
      return this.requestByRoute("mandates.status");
    }

    async createMandateDraft(body: MandateRuleInput): Promise<MandateRevisionView> {
      return this.postEnvelope(resolvePath("mandates.drafts.create"), body);
    }

    async proposeMandateAmendment(body: MandateRuleInput): Promise<MandateRevisionView> {
      return this.postEnvelope(resolvePath("mandates.amendments.propose"), body);
    }

    // body는 password/totpCode가 있을 때만 서버가 reauthenticate()를 거쳐 material
    // change 게이트를 통과시킨다(mandates.py:26-32) — 없으면 기본값 {}로 non-material
    // 변경만 허용된다.
    async activateMandateRevision(
      revisionId: string,
      body: ActivateRevisionRequest = {},
    ): Promise<MandateRevisionView> {
      const path = resolvePath("mandates.revisions.activate").replace(":revisionId", revisionId);
      return this.postEnvelope(path, body);
    }

    async pauseMandate(): Promise<MandateRevisionView> {
      return this.postEnvelope(resolvePath("mandates.mandate.pause"));
    }

    async resumeMandate(): Promise<MandateRevisionView> {
      return this.postEnvelope(resolvePath("mandates.mandate.resume"));
    }

    async evaluateMandatePolicy(body: PolicyEvaluationSubject): Promise<PolicyDecisionView> {
      return this.postEnvelope(resolvePath("mandates.policy.evaluate"), body);
    }
  };
}
