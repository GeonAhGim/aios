import type { BeginConnectionRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2346(FE-OPS-5): 계정 연동(connections) 목록 조회 + 생성(begin)/confirm/sync/
// revoke 훅. useTrustStatus/useRevokeConsent와 동일 관용(useQuery/useMutation +
// apiClient 싱글턴). 4개 mutation 전부 연동 상태를 바꾸므로(생성은 새 항목 추가,
// confirm/sync/revoke는 기존 항목 상태 전이) 성공 시 CONNECTIONS_QUERY_KEY를
// 무효화해 GET 재조회로 화면을 갱신한다(decision: 서버가 SSOT, TrustPage의
// 동의 철회와 동일하게 낙관적 갱신을 쓰지 않는다).
const CONNECTIONS_QUERY_KEY = ["connections"];

export function useConnections() {
  return useQuery({
    queryKey: CONNECTIONS_QUERY_KEY,
    queryFn: () => apiClient.listConnections(),
  });
}

export function useBeginConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BeginConnectionRequest) => apiClient.beginConnection(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY }),
  });
}

export function useConfirmConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) => apiClient.confirmConnection(connectionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY }),
  });
}

export function useSyncConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) => apiClient.syncConnection(connectionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY }),
  });
}

export function useRevokeConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) => apiClient.revokeConnection(connectionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY }),
  });
}
