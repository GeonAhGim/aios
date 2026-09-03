import type {
  CandleResponse,
  GeneratedConditions,
  IndicatorComputeResponse,
  IndicatorListResponse,
  PreviewRequest,
  PreviewResponse,
  PromptGenerateRequest,
  StrategyCreateRequest,
  StrategyDetailResponse,
  StrategyResponse,
  StrategySummary,
  WizardGenerateRequest,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-14 전략 편집기 — strategy-builder 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// task-1160: 치환·쿼리 없는 단순 조회 2건(listIndicators/listMyStrategies)은
// requestByRoute로, 쿼리·:name·:strategyId/:version 치환이 있는 조회 3건은
// resolvePath로 경로를 만들고 resolveEnvelope(route)로 request/requestEnvelope
// 분기만 apiPaths.ts 레지스트리 단일 출처로 이관했다(admin.ts task-1159 선례와
// 동일 관용) — 분기 결과 자체는 바꾸지 않는다.
export function withStrategyBuilder<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listIndicators(): Promise<IndicatorListResponse> {
      return this.requestByRoute("strategyBuilder.indicators.list");
    }

    async listMyStrategies(): Promise<StrategySummary[]> {
      return this.requestByRoute("strategyBuilder.strategies.base");
    }

    async getCandles(params: {
      exchange: string;
      symbol: string;
      timeframe?: string;
      limit?: number;
    }): Promise<CandleResponse[]> {
      const path = this.withQuery(resolvePath("strategyBuilder.candles"), params);
      return resolveEnvelope("strategyBuilder.candles") ? this.requestEnvelope(path) : this.request(path);
    }

    async computeIndicator(
      name: string,
      params: { exchange: string; symbol: string; timeframe?: string; period?: number },
    ): Promise<IndicatorComputeResponse> {
      const path = this.withQuery(
        resolvePath("strategyBuilder.indicators.compute").replace(":name", name),
        params,
      );
      return resolveEnvelope("strategyBuilder.indicators.compute")
        ? this.requestEnvelope(path)
        : this.request(path);
    }

    async createStrategy(body: StrategyCreateRequest): Promise<StrategyResponse> {
      return this.post(resolvePath("strategyBuilder.strategies.base"), body);
    }

    async getStrategy(strategyId: string, version: string): Promise<StrategyDetailResponse> {
      const path = resolvePath("strategyBuilder.strategies.get")
        .replace(":strategyId", strategyId)
        .replace(":version", version);
      return resolveEnvelope("strategyBuilder.strategies.get") ? this.requestEnvelope(path) : this.request(path);
    }

    async previewStrategy(body: PreviewRequest): Promise<PreviewResponse> {
      return this.post(resolvePath("strategyBuilder.preview"), body);
    }

    async generateWizardStrategy(body: WizardGenerateRequest): Promise<GeneratedConditions> {
      return this.post(resolvePath("strategyBuilder.wizard"), body);
    }

    async generateFromPrompt(body: PromptGenerateRequest): Promise<GeneratedConditions> {
      return this.post(resolvePath("strategyBuilder.generateFromPrompt"), body);
    }
  };
}
