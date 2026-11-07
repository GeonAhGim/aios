import type {
  ExecutionCreateRequest,
  ExpandExposureRequest,
  LiveConversionRequest,
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
// useIdempotentSubmit으로 키 수명주기를 관리해 넘긴다). J8 LP-3: 손실 한도·초기 노출·재인증 필수.
export function useLiveConversion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      executionId,
      body,
      idempotencyKey,
    }: {
      executionId: number;
      body: LiveConversionRequest;
      idempotencyKey: string;
    }) => apiClient.liveConversion(executionId, body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
  });
}

// J8 LP-2: 전환 근거(모의 운용 실적). 전환 대화상자가 열려 있을 때만 조회한다.
export function useLiveReadiness(executionId: number, enabled = true) {
  return useQuery({
    queryKey: ["executions", executionId, "live-readiness"],
    queryFn: () => apiClient.getLiveReadiness(executionId),
    enabled,
  });
}

// J8 LP-4: 초기 노출 단계의 허용 자본 확대(확대만 가능, 재인증 필수).
export function useExpandExposure() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ executionId, body }: { executionId: number; body: ExpandExposureRequest }) =>
      apiClient.expandExposure(executionId, body),
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
