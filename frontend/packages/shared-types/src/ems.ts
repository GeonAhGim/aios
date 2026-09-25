// src/foundation/ems/contracts/v1.py 기준 — AlgoProgressView, TcaResultView, ComputeTcaRequest

export interface AlgoProgressResponse {
  parentId: string;
  status: string;
  totalSlices: number;
  submittedSlices: number;
  pendingSlices: number;
  remainingQty: string;
  demotedToTwap: boolean;
  demotionReason: string | null;
}

export interface TcaResultBreakdown {
  arrivalBps: string;
  vwapBps: string;
  impactBps: string;
  feesBps: string;
  opportunityBps: string;
  schemaVersion: string;
}

export interface TcaResultResponse {
  parentId: string;
  revision: number;
  result: TcaResultBreakdown;
  computedAt: string;
}

export interface ComputeTcaFill {
  price: string | number;
  qty: string | number;
}

export interface ComputeTcaBar {
  close: string | number;
  volume: string | number;
}

export type OrderSide = "BUY" | "SELL";

export interface ComputeTcaRequest {
  side: OrderSide;
  fills: ComputeTcaFill[];
  priceAtArrivalTs: string | number;
  bars: ComputeTcaBar[];
  spreadCost: string | number;
  fees: string | number;
  totalCost: string | number;
  computedAt: string;
  revision?: number;
}
