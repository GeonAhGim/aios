import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function usePortfolio() {
  return useQuery({ queryKey: ["portfolio"], queryFn: () => apiClient.getPortfolio() });
}

export function useExecutions() {
  // FD-16.4 실시간성 요구 — 5초 폴링(Draft, 17번 문서 §17.4와 동일 원칙).
  return useQuery({
    queryKey: ["executions"],
    queryFn: () => apiClient.listExecutions(),
    refetchInterval: 5000,
  });
}
