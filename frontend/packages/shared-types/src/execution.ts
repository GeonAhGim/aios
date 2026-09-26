// src/api/schemas/execution.py 1:1 대응.

export type ExecutionMode = "PAPER" | "LIVE";

export interface ExecutionCreateRequest {
  strategyId: string;
  strategyVersion: string;
  allocatedCapital: string;
  currency: string;
  exchange: string;
  mode: ExecutionMode;
}

export interface ExecutionResponse {
  id: number;
  status: string;
  mode: ExecutionMode;
  exchange: string;
  allocatedCapital: string;
  approvalRequestId: number | null;
  maxDrawdownPct: string | null;
}

// execution_monitoring_service.py LastRiskVerdict 1:1 대응 —
// risk_decision(R-24 WORM 원장)에서 조회한, 해당 실행의 가장 최근 리스크/
// 컴플라이언스 게이트 판정.
export interface LastRiskVerdict {
  outcome: string;
  reasonCodes: string[];
  evaluatedAt: string;
}

export interface ExecutionCardResponse {
  executionId: number;
  strategyId: string;
  strategyVersion: string;
  status: string;
  mode: ExecutionMode;
  exchange: string;
  allocatedCapital: string;
  daysSinceStart: number | null;
  realizedPnl: string;
  unrealizedPnl: string;
  maxDrawdownPct: string | null;
  lastRiskVerdict: LastRiskVerdict | null;
}

export interface SetMaxDrawdownRequest {
  maxDrawdownPct: string | null;
}
