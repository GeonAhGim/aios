import type {
  ExecutionCreateRequest,
  RebalanceRequest,
  SetMaxDrawdownRequest,
} from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function usePortfolio() {
  return useQuery({ queryKey: ["portfolio"], queryFn: () => apiClient.getPortfolio() });
}

export function useRebalancePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      body,
      idempotencyKey,
    }: {
      body: RebalanceRequest;
      idempotencyKey: string;
    }) => apiClient.rebalancePortfolio(body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio"] }),
  });
}

export function useReport(periodStart: string, periodEnd: string, executionId?: number) {
  return useQuery({
    queryKey: ["report", periodStart, periodEnd, executionId],
    queryFn: () => apiClient.generateReport(periodStart, periodEnd, executionId),
    enabled: !!periodStart && !!periodEnd,
  });
}

export function useExecutions() {
  // FD-16.4 실시간성 요구 — 5초 폴링(Draft, 17번 문서 §17.4와 동일 원칙).
  return useQuery({
    queryKey: ["executions"],
    queryFn: () => apiClient.listExecutions(),
    refetchInterval: 5000,
  });
}

export function useCreateExecution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      body,
      idempotencyKey,
    }: {
      body: ExecutionCreateRequest;
      idempotencyKey?: string;
    }) => apiClient.createExecution(body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

export function useStartExecution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      executionId,
      idempotencyKey,
    }: {
      executionId: number;
      idempotencyKey: string;
    }) => apiClient.startExecution(executionId, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

// spec §9 PLT-15: 실행을 LIVE로 전환하는 금전 라우트 — idempotencyKey 필수(호출부가
// useIdempotentSubmit으로 키 수명주기를 관리해 넘긴다).
export function useConvertToLive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      executionId,
      body,
      idempotencyKey,
    }: {
      executionId: number;
      body: { allocatedCapital: string; currency: string; exchange: string };
      idempotencyKey: string;
    }) => apiClient.convertToLive(executionId, body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

export function usePauseExecution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (executionId: number) => apiClient.pauseExecution(executionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

export function useSetExecutionRiskGuard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      executionId,
      body,
    }: {
      executionId: number;
      body: SetMaxDrawdownRequest;
    }) => apiClient.setExecutionRiskGuard(executionId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

export function useRetireExecution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      executionId,
      liquidation,
    }: {
      executionId: number;
      liquidation?: "IMMEDIATE_MARKET" | "KEEP_POSITIONS";
    }) => apiClient.retireExecution(executionId, liquidation),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}
