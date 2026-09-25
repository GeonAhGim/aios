import { useMutation } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2668(CM-18): 판정 ID로 단건 조회(explain, task-2618 GET /decisions/{id}).
// mandateStatus처럼 마운트 시 자동 조회하지 않는다 — 운영자가 임의 판정 ID를
// 입력해 조회를 트리거하는 화면이라 useQuery가 아니라 useMutation을 쓴다
// (ComplianceDecisionPanel의 useEvaluateMandatePolicy와 동일 관용).
export function useComplianceDecisionLookup() {
  return useMutation({
    mutationFn: (decisionId: string) => apiClient.getComplianceDecision(decisionId),
  });
}
