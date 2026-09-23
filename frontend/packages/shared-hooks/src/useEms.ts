import { useMutation, useQuery } from "@tanstack/react-query";
import type { ComputeTcaRequest, TcaResultResponse } from "@aios/shared-types";
import { apiClient } from "./clientInstance";

// EM-18: 알고리즘 집행 진행률 및 TCA 분석 훅.

export function useAlgoProgress(parentId: string) {
  return useQuery({
    queryKey: ["algoProgress", parentId],
    queryFn: () => apiClient.getAlgoProgress(parentId),
    enabled: !!parentId,
    refetchInterval: 5000,
  });
}

export function useLatestTca(parentId: string) {
  return useQuery({
    queryKey: ["latestTca", parentId],
    queryFn: () => apiClient.getLatestTca(parentId),
    enabled: !!parentId,
  });
}

export function useTcaRevision(parentId: string, revision: number) {
  return useQuery({
    queryKey: ["tcaRevision", parentId, revision],
    queryFn: () => apiClient.getTcaRevision(parentId, revision),
    enabled: !!parentId && revision > 0,
  });
}

export function useComputeTca() {
  return useMutation({
    mutationFn: ({
      parentId,
      request,
    }: {
      parentId: string;
      request: ComputeTcaRequest;
    }) => apiClient.computeTca(parentId, request),
  });
}
