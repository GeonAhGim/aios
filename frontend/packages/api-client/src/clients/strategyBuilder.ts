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
import type { AnyConstructor } from "../http";

// FD-14 전략 편집기 — strategy-builder 라우터는 봉투 미적용, 기존 경로 유지.
export function withStrategyBuilder<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listIndicators(): Promise<IndicatorListResponse> {
      return this.request("/strategy-builder/indicators");
    }

    async listMyStrategies(): Promise<StrategySummary[]> {
      return this.request("/strategy-builder/strategies");
    }

    async getCandles(params: {
      exchange: string;
      symbol: string;
      timeframe?: string;
      limit?: number;
    }): Promise<CandleResponse[]> {
      return this.request(this.withQuery("/strategy-builder/candles", params));
    }

    async computeIndicator(
      name: string,
      params: { exchange: string; symbol: string; timeframe?: string; period?: number },
    ): Promise<IndicatorComputeResponse> {
      return this.request(
        this.withQuery(`/strategy-builder/indicators/${name}/compute`, params),
      );
    }

    async createStrategy(body: StrategyCreateRequest): Promise<StrategyResponse> {
      return this.post("/strategy-builder/strategies", body);
    }

    async getStrategy(strategyId: string, version: string): Promise<StrategyDetailResponse> {
      return this.request(`/strategy-builder/strategies/${strategyId}/${version}`);
    }

    async previewStrategy(body: PreviewRequest): Promise<PreviewResponse> {
      return this.post("/strategy-builder/preview", body);
    }

    async generateWizardStrategy(body: WizardGenerateRequest): Promise<GeneratedConditions> {
      return this.post("/strategy-builder/wizard", body);
    }

    async generateFromPrompt(body: PromptGenerateRequest): Promise<GeneratedConditions> {
      return this.post("/strategy-builder/generate-from-prompt", body);
    }
  };
}
