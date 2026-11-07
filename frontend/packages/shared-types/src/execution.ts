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

// src/api/schemas/execution.py LiveConversionRequest 1:1 대응 (J8 LP-3) -- 모의 -> 실전 전환.
// 손실 한도·초기 노출·재인증(비밀번호, MFA 사용자는 TOTP)이 모두 필수다.
export interface LiveConversionRequest {
  allocatedCapital: string;
  currency: string;
  exchange: string;
  maxLossAmount: string;
  initialExposurePct: string;
  password: string;
  totpCode?: string | null;
}

// src/api/schemas/execution.py ExpandExposureRequest 1:1 대응 (J8 LP-4) -- 확대만 가능하다.
export interface ExpandExposureRequest {
  exposurePct: string;
  currency: string;
  exchange: string;
  password: string;
  totpCode?: string | null;
}

// src/api/schemas/live_readiness.py 1:1 대응 (J8 LP-2) -- 값이 없는 항목은 null이고
// 사유가 unavailable에 있다. 없는 데이터를 좋은 성과로 보이게 하지 않는다.
export interface LiveReadinessResponse {
  orderCount: number;
  terminalCount: number;
  openCount: number;
  unknownCount: number;
  filledCount: number;
  partialFillCount: number;
  unfilledCount: number;
  rejectedCount: number;
  fillRatio: string | null;
  partialFillRatio: string | null;
  unfilledRatio: string | null;
  rejectRatio: string | null;
  periodStart: string | null;
  periodEnd: string | null;
  periodDays: string | null;
  avgSlippageBps: string | null;
  slippageSampleSize: number;
  feeTotals: Record<string, string> | null;
  netPnl: string | null;
  recoveryEventCount: number | null;
  unavailable: Record<string, string>;
}

export interface SetMaxDrawdownRequest {
  maxDrawdownPct: string | null;
}
