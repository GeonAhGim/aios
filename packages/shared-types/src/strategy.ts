// src/api/schemas/strategy_builder.py, src/services/preview_service.py 1:1 대응.

export type ConditionOperator =
  | ">"
  | "<"
  | ">="
  | "<="
  | "=="
  | "crosses_above"
  | "crosses_below";

export interface PreviewCondition {
  indicator: string;
  params: Record<string, number>;
  operator: ConditionOperator;
  threshold: number;
}

export interface IndicatorListResponse {
  indicators: string[];
}

export interface IndicatorComputeResponse {
  indicator: string;
  values: (number | null)[];
  series: Record<string, (number | null)[]> | null;
  params: Record<string, number>;
  message: string | null;
}

export interface StrategyCreateRequest {
  strategyId: string;
  version: string;
  targetAsset: string;
  market: string;
  exchange: string;
  entryConditions: PreviewCondition[];
  exitConditions: PreviewCondition[];
  stopLossConditions: PreviewCondition[];
  entryCombine?: "AND" | "OR";
  exitCombine?: "AND" | "OR";
  stopLossCombine?: "AND" | "OR";
}

export interface StrategyResponse {
  strategyId: string;
  version: string;
  status: string;
  fsmDefinition: unknown;
}

export interface StrategyDetailResponse {
  strategyId: string;
  version: string;
  targetAsset: string;
  market: string;
  exchange: string;
  status: string;
  fsmDefinition: unknown;
}

export interface PreviewRequest {
  exchange: string;
  symbol: string;
  timeframe?: string;
  limit?: number;
  conditions: PreviewCondition[];
  combine?: "AND" | "OR";
}

export interface PreviewResponse {
  signalIndices: number[];
  signalTimes: string[];
  disclaimer: string;
  message: string | null;
}

// ADR-2026-08-29 §3 — 목표기반 마법사 + 자연어 프롬프트(현재 미구현).
export type StrategyGoal = "STEADY_GROWTH" | "AGGRESSIVE_GROWTH" | "HEDGE";
export type RiskTolerance = "LOW" | "MEDIUM" | "HIGH";

export interface WizardGenerateRequest {
  goal: StrategyGoal;
  riskTolerance: RiskTolerance;
}

export interface PromptGenerateRequest {
  prompt: string;
}

export interface GeneratedConditions {
  entryConditions: PreviewCondition[];
  exitConditions: PreviewCondition[];
  stopLossConditions: PreviewCondition[];
  entryCombine: "AND" | "OR";
  exitCombine: "AND" | "OR";
  stopLossCombine: "AND" | "OR";
  explanation: string;
}
