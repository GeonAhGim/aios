// BT-13(task-1607) 차트 즉시 백테스트 패널이 쓰는 유일한 호출부.
// Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.5 BT-10/BT-13.
// 서버 SSOT: src/api/routers/backtests.py(BT-10c, task-1619 9ccb238)
// `POST /v1/backtests/quick` -> `ApiResponse[QuickBacktestResultView]`, ok() 봉투.
// 요청/응답 필드는 src/api/schemas/backtests.py(QuickBacktestRequest/
// QuickBacktestResultView)를 그대로 옮긴 것이다 — Decimal 필드는 그 스키마 파일
// 주석대로 문자열로 오간다(별도 인코더 없음).
//
// 동기 실행·무저장(backtests.py 라우터 docstring decision) — 백테스트 결과를
// 어디에도 쓰지 않으므로 멱등키 대상이 아니다(scripts.ts와 동일 사유,
// postEnvelope로 충분). 경로 문자열은 apiPaths.ts 레지스트리에만 있다.
// positions.ts/marketData.ts/charting.ts와 동일 관용으로 AiosApiClient 합성에는
// 얹지 않고(client.ts 변경 없음) 화면이 createBacktestsClient로 직접 만든다.
import type { Timeframe, Venue } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import { ApiClientBase } from "../http";

export type BacktestSlippageModel =
  | { kind: "fixed"; bps: number }
  | { kind: "percent"; pct: number }
  | { kind: "volume_impact"; k: number; participationCap: number };

export interface BacktestCommissionInput {
  venue: string;
  makerBps: number;
  takerBps: number;
  minFee: number;
}

// src/foundation/backtest/domain/models_v2.py BacktestConfigV2 1:1 대응(schema_version은
// 서버 기본값에 맡기고 여기서 보내지 않는다). BT-13은 현실성 파라미터를 사용자에게
// 노출하지 않는 "즉시" 프리셋 하나만 쓴다 — 상세 구성 화면은 BT-1의 몫이라 이 타입은
// BacktestPanel.tsx의 buildQuickConfig가 채우는 고정 프리셋 모양만 표현한다.
export interface BacktestConfigV2Input {
  slippage: BacktestSlippageModel;
  commission: BacktestCommissionInput;
  latencyMs: number;
  partialFill: { maxParticipationPct: number };
  orderTypes: { limit: boolean; stop: boolean; oco: boolean; trailing: boolean };
  magnifierTf: Timeframe | null;
  costs: { funding: boolean; borrowApr?: number | null };
  adjustments: { splits: boolean; dividends: boolean };
  calendar: "session" | "24x7";
}

export interface QuickBacktestRequestInput {
  venue: Venue;
  symbol?: string;
  instrumentId?: string;
  timeframe: Timeframe;
  start: string;
  end: string;
  asOf?: string;
  /** Decimal 문자열(schemas/backtests.py 주석과 동일 관용) — 부동소수 변환 없이 그대로 보낸다. */
  initialCash: string;
  fundingRate?: string;
  config: BacktestConfigV2Input;
  scriptSource: string;
}

export interface BacktestFillView {
  barIndex: number;
  openTime: string;
  side: "BUY" | "SELL";
  orderType: "market" | "limit" | "stop";
  quantity: string;
  price: string;
  commission: string;
  remainingQuantity: string;
}

export interface QuickBacktestResultView {
  fills: BacktestFillView[];
  equityCurve: string[];
  finalEquity: string;
  cash: string;
  positionQuantity: string;
  fundingCost: string;
  borrowCost: string;
  bars: number;
  expiredOrders: number;
  warnings: string[];
}

class BacktestsApiClient extends ApiClientBase {
  async runQuickBacktest(input: QuickBacktestRequestInput): Promise<QuickBacktestResultView> {
    return this.postEnvelope(resolvePath("backtests.quick"), input);
  }
}

export interface BacktestsClient {
  runQuickBacktest(input: QuickBacktestRequestInput): Promise<QuickBacktestResultView>;
}

export function createBacktestsClient(baseUrl: string, getToken: () => string | null): BacktestsClient {
  const client = new BacktestsApiClient(baseUrl, getToken);
  return { runQuickBacktest: (input) => client.runQuickBacktest(input) };
}
