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
import { isRouteImplemented, resolvePath, type ApiRouteName } from "../apiPaths";
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

// BT-18(task-2428) — 파라미터 그리드 스윕 결과 조회. 서버 SSOT는 아직 순수 함수뿐이다
// (src/foundation/backtest/vector/grid.py::sweep_grid_and_record, domain/param_stability.py
// ::stability_score) — apiRoutes.ts의 "backtests.sweep" 등록 주석대로 이를 감싸는 API
// 라우터가 없어 implemented=false(유령 경로)다. 아래 타입은 그 두 순수 함수의 반환 모양을
// 그대로 옮긴 것: GridSweepResult(combo_key -> QuickBacktestResult) + ExperimentLedgerEntry
// (combo_key/combo_index/seed/reproducibility_key) + ParameterStabilityReport(best/
// neighbor_mean/neighbor_std/isolated). 라우터가 생기면 실제 응답 스키마와 대조해 고친다.
export interface SweepAxisInput {
  /** param_stability.py ParamGrid.axes의 키 — 축 이름(예: "rsi_len"). */
  name: string;
  /** 오름차순·중복 없는 값(ParamGrid.__post_init__ 불변조건과 동일). */
  values: number[];
}

export interface SweepComboInput {
  /** grid.py GridSweepResult.results의 키(예: "rsi_len=14,exit=20"). */
  comboKey: string;
  /** axes 각각에 대응하는 값 하나씩 — param_stability.py의 Point를 이름으로 표현한 것. */
  axisValues: Record<string, number>;
  /** 이 콤보가 컴파일된 DSL 스크립트의 해시(grid.py sweep_grid_and_record의 script_hashes). */
  scriptHash: string;
  scriptSource: string;
}

export interface SweepRequestInput {
  venue: Venue;
  symbol?: string;
  instrumentId?: string;
  timeframe: Timeframe;
  start: string;
  end: string;
  /** Decimal 문자열(QuickBacktestRequestInput과 동일 관용). */
  initialCash: string;
  fundingRate?: string;
  config: BacktestConfigV2Input;
  axes: SweepAxisInput[];
  combos: SweepComboInput[];
  /** 히트맵·안정성 표면이 비교할 스칼라 지표(예: "finalEquity"). */
  metric: string;
  dataLineageHash: string;
  rollupVersion: string;
  seed: number;
}

export interface SweepPointResultView {
  comboKey: string;
  /** ExperimentLedgerEntry.combo_index — combos 순서를 그대로 복원할 수 있다. */
  comboIndex: number;
  axisValues: Record<string, number>;
  /** Decimal 문자열 — QuickBacktestResultView.finalEquity 등과 동일 관용. */
  metricValue: string;
  reproducibilityKey: string;
  seed: number;
}

export interface SweepStabilityView {
  bestAxisValues: Record<string, number>;
  neighborMean: string;
  neighborStd: string;
  isolated: boolean;
}

export interface SweepResultView {
  axes: SweepAxisInput[];
  metric: string;
  points: SweepPointResultView[];
  /** 축이 정확히 2개이고 그리드가 param_stability.py MIN_GRID_SIZE(4) 이상일 때만 채워진다. */
  stability: SweepStabilityView | null;
  warnings: string[];
}

const SWEEP_ROUTE: ApiRouteName = "backtests.sweep";

// sessions.ts(task-1325) SessionsRouteNotImplementedError와 동일 패턴 — 유령 경로를
// typed 오류로 노출해 호출부(SweepResultsPage.tsx)가 문자열 매칭 대신 instanceof로
// "아직 없는 라우트"를 판별하게 한다.
export class SweepRouteNotImplementedError extends Error {
  readonly route: ApiRouteName = SWEEP_ROUTE;

  constructor() {
    super("파라미터 스윕 결과 API가 아직 제공되지 않습니다.");
    this.name = "SweepRouteNotImplementedError";
  }
}

class BacktestsApiClient extends ApiClientBase {
  async runQuickBacktest(input: QuickBacktestRequestInput): Promise<QuickBacktestResultView> {
    return this.postEnvelope(resolvePath("backtests.quick"), input);
  }

  async runSweep(input: SweepRequestInput): Promise<SweepResultView> {
    if (!isRouteImplemented(SWEEP_ROUTE)) {
      throw new SweepRouteNotImplementedError();
    }
    return this.postEnvelope(resolvePath(SWEEP_ROUTE), input);
  }
}

export interface BacktestsClient {
  runQuickBacktest(input: QuickBacktestRequestInput): Promise<QuickBacktestResultView>;
  runSweep(input: SweepRequestInput): Promise<SweepResultView>;
}

export function createBacktestsClient(baseUrl: string, getToken: () => string | null): BacktestsClient {
  const client = new BacktestsApiClient(baseUrl, getToken);
  return {
    runQuickBacktest: (input) => client.runQuickBacktest(input),
    runSweep: (input) => client.runSweep(input),
  };
}
