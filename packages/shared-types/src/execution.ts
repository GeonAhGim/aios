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
}

export interface SetMaxDrawdownRequest {
  maxDrawdownPct: string | null;
}
