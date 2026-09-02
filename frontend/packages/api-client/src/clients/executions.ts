import type {
  ExecutionCardResponse,
  ExecutionCreateRequest,
  ExecutionResponse,
  SetMaxDrawdownRequest,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-16 실행 제어판 — executions.py 라우터는 봉투 미적용, 기존 경로 유지.
export function withExecutions<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listExecutions(): Promise<ExecutionCardResponse[]> {
      return this.request("/executions");
    }

    async createExecution(
      body: ExecutionCreateRequest,
      idempotencyKey?: string,
    ): Promise<ExecutionResponse> {
      return this.postIdempotent("/executions", body, idempotencyKey);
    }

    async startExecution(executionId: number, idempotencyKey?: string): Promise<ExecutionResponse> {
      return this.postIdempotent(`/executions/${executionId}/start`, undefined, idempotencyKey);
    }

    async pauseExecution(executionId: number): Promise<ExecutionResponse> {
      return this.post(`/executions/${executionId}/pause`);
    }

    async retireExecution(
      executionId: number,
      liquidation: "IMMEDIATE_MARKET" | "KEEP_POSITIONS" = "KEEP_POSITIONS",
    ): Promise<ExecutionResponse> {
      return this.post(`/executions/${executionId}/retire`, { liquidation });
    }

    async convertToLive(
      executionId: number,
      body: { allocatedCapital: string; currency: string; exchange: string },
      idempotencyKey?: string,
    ): Promise<ExecutionResponse> {
      return this.postIdempotent(`/executions/${executionId}/convert-to-live`, body, idempotencyKey);
    }

    async setExecutionRiskGuard(
      executionId: number,
      body: SetMaxDrawdownRequest,
    ): Promise<ExecutionResponse> {
      return this.patch(`/executions/${executionId}/risk-guard`, body);
    }
  };
}
