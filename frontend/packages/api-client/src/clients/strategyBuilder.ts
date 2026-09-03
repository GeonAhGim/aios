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
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-14 전략 편집기 — strategy-builder 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
export function withStrategyBuilder<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listIndicators(): Promise<IndicatorListResponse> {
      return this.request(resolvePath("strategyBuilder.indicators.list"));
    }

    async listMyStrategies(): Promise<StrategySummary[]> {
      return this.request(resolvePath("strategyBuilder.strategies.base"));
    }

    async getCandles(params: {
      exchange: string;
      symbol: string;
      timeframe?: string;
      limit?: number;
    }): Promise<CandleResponse[]> {
      return this.request(this.withQuery(resolvePath("strategyBuilder.candles"), params));
    }

    async computeIndicator(
      name: string,
      params: { exchange: string; symbol: string; timeframe?: string; period?: number },
    ): Promise<IndicatorComputeResponse> {
      return this.request(
        this.withQuery(resolvePath("strategyBuilder.indicators.compute").replace(":name", name), params),
      );
    }

    async createStrategy(body: StrategyCreateRequest): Promise<StrategyResponse> {
      return this.post(resolvePath("strategyBuilder.strategies.base"), body);
    }

    async getStrategy(strategyId: string, version: string): Promise<StrategyDetailResponse> {
      return this.request(
        resolvePath("strategyBuilder.strategies.get").replace(":strategyId", strategyId).replace(":version", version),
      );
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
