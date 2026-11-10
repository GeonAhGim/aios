import type {
  DecisionTrailResponse,
  ExecutionCardResponse,
  ExecutionCreateRequest,
  ExecutionResponse,
  ExpandExposureRequest,
  LiveConversionRequest,
  LiveReadinessResponse,
  SetMaxDrawdownRequest,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
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
    // J8 LP-3: 모의 -> 실전 전환. 손실 한도·초기 노출·재인증이 없으면 서버가 거부한다.
    async liveConversion(
      executionId: number,
      body: LiveConversionRequest,
      idempotencyKey: string,
    ): Promise<ExecutionResponse> {
      const path = resolvePath("executions.liveConversion").replace(":executionId", String(executionId));
      return this.postIdempotent(path, body, idempotencyKey);
    }

    // J8 LP-2: 전환 근거(모의 운용 실적). 값이 없는 항목은 null + unavailable 사유다.
    async getLiveReadiness(executionId: number): Promise<LiveReadinessResponse> {
      const path = resolvePath("executions.liveReadiness").replace(":executionId", String(executionId));
      return resolveEnvelope("executions.liveReadiness")
        ? this.requestEnvelope(path)
        : this.request(path);
    }

    // J8 LP-4: 초기 노출 단계에서 허용 자본을 늘린다(확대만 가능, 재인증 필수).
    // 요청이 절대 목표 비율이라 같은 값을 다시 보내도 결과가 같아 멱등 표식은 두지 않는다.
    async expandExposure(
      executionId: number,
      body: ExpandExposureRequest,
    ): Promise<ExecutionResponse> {
      const path = resolvePath("executions.expandExposure").replace(":executionId", String(executionId));
      return this.post(path, body);
    }

    // 실행 한 건의 의사결정 이력(읽기 전용). 값이 없는 항목은 unavailable 사유로 온다.
    async getDecisionTrail(executionId: number): Promise<DecisionTrailResponse> {
      const path = resolvePath("executions.decisionTrail").replace(":executionId", String(executionId));
      return this.request(path);
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
