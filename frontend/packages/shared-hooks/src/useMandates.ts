import type { ActivateRevisionRequest, MandateRuleInput, PolicyEvaluationSubject } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2336(FE-OPS-2): 위임장(mandate) status·drafts·amendments·activate·pause·
// resume·policy:evaluate 훅. useSafetyControls(useRiskGate.ts)와 동일 관용
// (useQuery/useMutation + apiClient 싱글턴) — 새 데이터 페칭 패턴을 만들지 않는다.
const MANDATE_STATUS_QUERY_KEY = ["mandateStatus"];

export function useMandateStatus() {
  return useQuery({
    queryKey: MANDATE_STATUS_QUERY_KEY,
    queryFn: () => apiClient.getMandateStatus(),
  });
}

export function useCreateMandateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MandateRuleInput) => apiClient.createMandateDraft(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY }),
  });
}

export function useProposeMandateAmendment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MandateRuleInput) => apiClient.proposeMandateAmendment(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY }),
  });
}

export function useActivateMandateRevision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ revisionId, body }: { revisionId: string; body?: ActivateRevisionRequest }) =>
      apiClient.activateMandateRevision(revisionId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY }),
  });
}

export function usePauseMandate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.pauseMandate(),
    onSuccess: () => qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY }),
  });
}

export function useResumeMandate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.resumeMandate(),
    onSuccess: () => qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY }),
  });
}

// policy:evaluate는 mandate 상태를 바꾸지 않는(순수 조회) 커맨드라 mandateStatus
// 캐시를 무효화하지 않는다(evaluate_policy.py docstring: "부수효과: PolicyDecision
// 기록은 있지만 이벤트는 없음" — mandate/active_revision 자체는 그대로).
export function useEvaluateMandatePolicy() {
  return useMutation({
    mutationFn: (body: PolicyEvaluationSubject) => apiClient.evaluateMandatePolicy(body),
  });
}
