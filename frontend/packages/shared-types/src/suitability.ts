// src/services/suitability_questionnaire.py, src/api/schemas/suitability.py 1:1 대응.

export type InvestmentGoal = "SHORT_TERM_PROFIT" | "LONG_TERM_GROWTH";

export type LiquidityNeed = "WITHIN_1_YEAR" | "1_TO_3_YEARS" | "OVER_3_YEARS";

export interface SuitabilityAnswers {
  yearsOfExperience: number;
  investableRatioPct: number;
  lossTolerancePct: number;
  investmentGoal: InvestmentGoal;
  liquidityNeed: LiquidityNeed;
}

export type RiskProfile = "안정형" | "중립형" | "공격형";

export interface RiskProfileResponse {
  riskProfile: RiskProfile;
  assessedAt: string;
  nextReassessmentDue: string;
  isHigherRiskThanPrevious: boolean;
}

export interface RiskProfileHistoryEntry {
  riskProfile: RiskProfile;
  assessedAt: string;
  answers: Record<string, unknown>;
}
