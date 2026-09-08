import { parseValidationResultView, type StartValidationRequest, type ValidationResultView } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import type { AnyConstructor } from "../http";

// task-2412(FE-OPS-8): src/api/routers/foundation/validation.py 원문 기준 —
// POST /v1/foundation/validation-runs/{strategy_id}/{strategy_version}은
// ApiResponse[ValidationResultView]로 응답한다(envelope=true, apiRoutes.ts
// "validation.start"). requestEnvelope는 data를 camelCase로 바꾸지만
// parseValidationResultView는 계약 그대로 snake_case를 기대하므로
// keysToSnake로 되돌린다(positions.ts와 동일 관용, task-1524 decision).
export function withValidation<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async startValidation(
      strategyId: string,
      strategyVersion: string,
      body: StartValidationRequest,
    ): Promise<ValidationResultView | null> {
      const path = resolvePath("validation.start")
        .replace(":strategyId", strategyId)
        .replace(":strategyVersion", strategyVersion);
      const raw = await this.postEnvelope<unknown>(path, body);
      return parseValidationResultView(keysToSnake(raw));
    }
  };
}
