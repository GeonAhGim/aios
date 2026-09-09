import type { GrantMembershipBody } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2338(FE-OPS-4): 신뢰(trust) 상태 조회 + 동의 철회 + 멤버십 grant/suspend/revoke
// 훅. useReconciliationStates/useResolveReconciliation과 동일 관용(useQuery/
// useMutation + apiClient 싱글턴). 동의 철회는 낙관적 UI가 아니다 — mutation
// 성공만으로 화면 상태를 바꾸지 않고 TRUST_STATUS_QUERY_KEY를 무효화해 GET
// /status 재조회로 화면을 갱신한다(decision: 서버가 SSOT). 멤버십 grant/suspend/
// revoke는 대응하는 GET(목록) 라우트가 서버에 없어 무효화할 캐시가 없다 — 각
// mutation 응답(MembershipResponse)이 그 자체로 최신 상태다.
const TRUST_STATUS_QUERY_KEY = ["trustStatus"];

export function useTrustStatus() {
  return useQuery({
    queryKey: TRUST_STATUS_QUERY_KEY,
    queryFn: () => apiClient.getTrustStatus(),
  });
}

export function useRevokeConsent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (consentId: string) => apiClient.revokeConsent(consentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: TRUST_STATUS_QUERY_KEY }),
  });
}

export function useGrantMembership() {
  return useMutation({
    mutationFn: (body: GrantMembershipBody) => apiClient.grantMembership(body),
  });
}

export function useSuspendMembership() {
  return useMutation({
    mutationFn: (subjectId: string) => apiClient.suspendMembership(subjectId),
  });
}

export function useRevokeMembership() {
  return useMutation({
    mutationFn: (subjectId: string) => apiClient.revokeMembership(subjectId),
  });
}
