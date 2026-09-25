// src/foundation/performance/contracts/v1.py, src/api/schemas/foundation/performance.py
// 1:1 대응. task-5804(FE-OPS-6): 실적 명세서(performance-statements) compute·목록·
// 상세·correct 4라우트를 다루는 클라이언트의 타입만 담당한다.
//
// 금액·수익률은 원시 Decimal이 아니라 문자열로 넘어온다(ledgerView.ts와 동일 규칙) —
// Number 변환은 부동소수 오차를 만들므로 절대 하지 않는다. `amount: null`은 PENDING
// (대사 미완료로 값 없음, §3.4)을 뜻한다 — 0으로 대체하지 않는다("never assume zero").

export type StatementScope = "PAPER" | "LIVE";

export type StatementState = "ESTIMATED" | "FINAL" | "CORRECTED";

export interface MoneyValue {
  amount: string | null;
  currency: string;
  precision: number;
  asOf: string;
  state: "ESTIMATED" | "FINAL";
}

export interface ReturnValue {
  valuePct: string | null;
  basis: "GROSS" | "NET";
  method: "TWR" | "MWR";
  periodStart: string;
  periodEnd: string;
  annualized: boolean;
  periodsPerYear: number | null;
}

export interface ComponentBreakdown {
  grossPnl: MoneyValue;
  fees: MoneyValue;
  slippage: MoneyValue;
  funding: MoneyValue;
  fx: MoneyValue;
  cashflowsNet: MoneyValue;
  estimatedTax: MoneyValue;
  netPnl: MoneyValue;
}

export interface PerformanceStatementView {
  id: string;
  tenantId: string;
  scope: StatementScope;
  scopeRef: string;
  periodStart: string;
  periodEnd: string;
  asOf: string;
  methodologyVersion: string;
  methodologyHash: string;
  inputRefs: string[];
  components: ComponentBreakdown;
  returns: ReturnValue[];
  risk: Record<string, string | null>;
  benchmark: Record<string, string | null> | null;
  benchmarkRef: string | null;
  state: StatementState;
  revisionNo: number;
  priorStatementId: string | null;
  identityOk: boolean;
  identityResidual: string | null;
  limitations: string[];
  evidenceRefs: string[];
  schemaVersion: string;
}

export interface PerformanceStatementListResponse {
  statements: PerformanceStatementView[];
}

// POST :compute 요청 body — performance.py:73-74, scope=LIVE는 서버가
// UnsupportedStatementScopeError(422)로 거부한다(라이브 입력 어댑터 미구현).
export interface ComputeStatementRequest {
  scope: StatementScope;
  periodStart: string;
  periodEnd: string;
  methodologyVersion?: string;
}

// POST /{statement_id}:correct 요청 body.
export interface CorrectStatementRequest {
  reason: string;
}

export interface ListPerformanceStatementsParams {
  scope?: StatementScope;
  portfolioId?: string;
}

export interface GetPerformanceStatementParams {
  portfolioId?: string;
}
