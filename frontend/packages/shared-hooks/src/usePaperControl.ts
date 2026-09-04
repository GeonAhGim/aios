import type { RequestPaperDeploymentBody } from "@aios/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

const PAPER_DEPLOYMENTS_KEY = ["paperDeployments"];

export function usePaperDeployments() {
  return useQuery({
    queryKey: PAPER_DEPLOYMENTS_KEY,
    queryFn: () => apiClient.listPaperDeployments(),
  });
}

export function useRequestPaperDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      body,
      idempotencyKey,
    }: {
      body: RequestPaperDeploymentBody;
      idempotencyKey: string;
    }) => apiClient.requestPaperDeployment(body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: PAPER_DEPLOYMENTS_KEY }),
  });
}

// spec §9 PLT-15: 4개 명령(start/resume/pause/stop) 모두 idempotencyKey를 필수
// 인자로 받아 호출부(useIdempotentSubmit)가 키 수명주기를 직접 관리하도록
// 강제한다 — 4개가 모양이 완전히 같아(deploymentId + idempotencyKey) 팩토리로
// 묶는다(usePortfolio.ts의 useStartExecution 등 개별 선언 선례와 달리, 여기는
// 4개가 동일 파라미터·동일 무효화 대상이라 반복이 팩토리보다 덜 명확하다).
function useDeploymentCommand(
  command: (deploymentId: string, idempotencyKey: string) => Promise<unknown>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ deploymentId, idempotencyKey }: { deploymentId: string; idempotencyKey: string }) =>
      command(deploymentId, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: PAPER_DEPLOYMENTS_KEY }),
  });
}

export function useStartPaperDeployment() {
  return useDeploymentCommand((id, key) => apiClient.startPaperDeployment(id, key));
}

export function useResumePaperDeployment() {
  return useDeploymentCommand((id, key) => apiClient.resumePaperDeployment(id, key));
}

export function usePausePaperDeployment() {
  return useDeploymentCommand((id, key) => apiClient.pausePaperDeployment(id, key));
}

export function useStopPaperDeployment() {
  return useDeploymentCommand((id, key) => apiClient.stopPaperDeployment(id, key));
}
