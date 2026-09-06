import type { CompileScriptView } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// DSL-13a 프론트 스크립트 편집기가 쓰는 유일한 호출부.
// Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4
// DSL-12(선행, task-1535) — POST /v1/scripts/compile, ok() 봉투. 오류는 별도
// 응답 스키마가 없다(src/api/schemas/scripts.py 주석) — VALIDATION_INVALID_FIELD +
// details.code/line/col가 그대로 buildApiError(httpErrors.ts)의 ApiError.details로
// 온다. 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와
// 동일 관용). 저장이 없는 순수 컴파일이라 멱등키 대상이 아니다(postEnvelope로 충분).
export function withScripts<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async compileScript(source: string): Promise<CompileScriptView> {
      return this.postEnvelope(resolvePath("scripts.compile"), { source });
    }
  };
}
