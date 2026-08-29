// src/services/portfolio_service.py 1:1 대응.

export interface PortfolioAllocation {
  executionId: number;
  strategyId: string;
  strategyVersion: string;
  exchange: string;
  mode: string;
  status: string;
  allocatedCapital: string;
  totalPnl: string;
  currentValue: string;
  weightPct: string;
}

export interface PortfolioView {
  allocations: PortfolioAllocation[];
  unallocatedCash: string;
  unallocatedCashWeightPct: string;
  totalPortfolioValue: string;
}

export interface RebalanceAdjustmentRequest {
  executionId: number;
  newAllocatedCapital: string;
}

export interface RebalanceRequest {
  adjustments: RebalanceAdjustmentRequest[];
}

export interface RebalanceResult {
  adjusted: number;
  pendingApproval: number;
  approvalRequestIds: number[];
}

export interface DailyPnL {
  tradeDate: string;
  dailyPnl: string;
  cumulativePnl: string;
}

export interface StrategyContribution {
  strategyId: string;
  strategyVersion: string;
  realizedPnl: string;
  tradeCount: number;
}

export interface ReportSummary {
  periodStart: string;
  periodEnd: string;
  totalReturn: string;
  winRate: string | null;
  maxDrawdown: string;
  tradeCount: number;
  strategyContributions: StrategyContribution[];
  dailyPnl: DailyPnL[];
}
