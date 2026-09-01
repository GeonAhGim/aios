import type {
  PreviewRequest,
  PromptGenerateRequest,
  StrategyCreateRequest,
  WizardGenerateRequest,
} from "@aios/shared-types";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useIndicators() {
  return useQuery({ queryKey: ["indicators"], queryFn: () => apiClient.listIndicators() });
}

export function useMyStrategies() {
  return useQuery({ queryKey: ["myStrategies"], queryFn: () => apiClient.listMyStrategies() });
}

export function useCandles(params: {
  exchange: string;
  symbol: string;
  timeframe?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["candles", params],
    queryFn: () => apiClient.getCandles(params),
    retry: false,
  });
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

export function useGenerateWizardStrategy() {
  return useMutation({
    mutationFn: (body: WizardGenerateRequest) => apiClient.generateWizardStrategy(body),
  });
}

export function useGenerateFromPrompt() {
  return useMutation({
    mutationFn: (body: PromptGenerateRequest) => apiClient.generateFromPrompt(body),
  });
}
