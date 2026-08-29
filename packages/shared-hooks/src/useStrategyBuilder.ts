import type { PreviewRequest, StrategyCreateRequest } from "@aios/shared-types";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useIndicators() {
  return useQuery({ queryKey: ["indicators"], queryFn: () => apiClient.listIndicators() });
}

export function useCreateStrategy() {
  return useMutation({
    mutationFn: (body: StrategyCreateRequest) => apiClient.createStrategy(body),
  });
}

export function usePreviewStrategy() {
  return useMutation({
    mutationFn: (body: PreviewRequest) => apiClient.previewStrategy(body),
  });
}

export function useStrategy(strategyId: string | null, version: string | null) {
  return useQuery({
    queryKey: ["strategy", strategyId, version],
    queryFn: () => apiClient.getStrategy(strategyId as string, version as string),
    enabled: !!strategyId && !!version,
  });
}
