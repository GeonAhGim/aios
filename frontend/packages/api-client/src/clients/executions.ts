import type {
  ExecutionCardResponse,
  ExecutionCreateRequest,
  ExecutionResponse,
  SetMaxDrawdownRequest,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-16 실행 제어판 — executions.py 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketData.ts와 동일 관용).
// task-1160: listExecutions(치환·쿼리 없는 단순 조회)는 requestByRoute로 옮겨
// request/requestEnvelope 분기를 apiPaths.ts 레지스트리 단일 출처로 이관했다
// (admin.ts task-1159 선례와 동일 관용) — 분기 결과 자체는 바꾸지 않는다.
export function withExecutions<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listExecutions(): Promise<ExecutionCardResponse[]> {
      return this.requestByRoute("executions.base");
    }

    async createExecution(
      body: ExecutionCreateRequest,
      idempotencyKey?: string,
    ): Promise<ExecutionResponse> {
      return this.postIdempotent(resolvePath("executions.base"), body, idempotencyKey);
    }

    // spec §9 PLT-15: idempotencyKey를 필수 인자로 받아 호출부(useIdempotentSubmit)가
    // 키 수명주기를 직접 관리하도록 강제한다 — 누락 시 타입 에러.
    async startExecution(executionId: number, idempotencyKey: string): Promise<ExecutionResponse> {
      const path = resolvePath("executions.start").replace(":executionId", String(executionId));
      return this.postIdempotent(path, undefined, idempotencyKey);
    }

    async pauseExecution(executionId: number): Promise<ExecutionResponse> {
      return this.post(resolvePath("executions.pause").replace(":executionId", String(executionId)));
    }

    async retireExecution(
      executionId: number,
      liquidation: "IMMEDIATE_MARKET" | "KEEP_POSITIONS" = "KEEP_POSITIONS",
    ): Promise<ExecutionResponse> {
      const path = resolvePath("executions.retire").replace(":executionId", String(executionId));
      return this.post(path, { liquidation });
    }

    // spec §9 PLT-15: idempotencyKey를 필수 인자로 받아 호출부(useIdempotentSubmit)가
    // 키 수명주기를 직접 관리하도록 강제한다 — 누락 시 타입 에러.
    async convertToLive(
      executionId: number,
      body: { allocatedCapital: string; currency: string; exchange: string },
      idempotencyKey: string,
    ): Promise<ExecutionResponse> {
      const path = resolvePath("executions.convertToLive").replace(":executionId", String(executionId));
      return this.postIdempotent(path, body, idempotencyKey);
    }

    async setExecutionRiskGuard(
      executionId: number,
      body: SetMaxDrawdownRequest,
    ): Promise<ExecutionResponse> {
      const path = resolvePath("executions.riskGuard").replace(":executionId", String(executionId));
      return this.patch(path, body);
    }
  };
}
