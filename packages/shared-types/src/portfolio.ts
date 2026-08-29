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
