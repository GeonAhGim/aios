import type { StartValidationRequest } from "@aios/shared-types";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2412(FE-OPS-8): useEvaluateRecovery(useRiskGate.ts)와 동일 관용 — path
// 파라미터(strategyId/strategyVersion)와 body를 한 객체로 받는다. 결과는 목록을
// 다시 그리지 않는 1회성 실행 화면이라 invalidateQueries 대상이 없다.
export function useStartValidation() {
  return useMutation({
    mutationFn: ({
      strategyId,
      strategyVersion,
      body,
    }: {
      strategyId: string;
      strategyVersion: string;
      body: StartValidationRequest;
    }) => apiClient.startValidation(strategyId, strategyVersion, body),
  });
}
