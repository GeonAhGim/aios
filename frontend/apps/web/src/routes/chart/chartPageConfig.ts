// CH-6a 순수 이동(task-2396): ChartPage.tsx의 API 클라이언트 싱글턴·기본값·주입
// 포트 타입(ChartPageProps)만 옮긴다 — 동작 변경 없음.
import type { ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { CandleQueryParams, CandleQueryResult, CoverageQueryParams, CoverageSpanView } from "@aios/api-client";
import { createBacktestsClient, createChartingClient, createMarketDataClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import type { Timeframe, Venue } from "@aios/shared-types";
import type { RunQuickBacktest } from "./BacktestPanel";
import type { ChartTemplatesPort } from "./ChartTemplates";
import type { ServerIndicatorSeriesPort } from "./IndicatorParityPanel";

export const VISIBLE_CANDLE_COUNT = 200;
export const DEFAULT_VENUE: Venue = "BITGET";
export const DEFAULT_TIMEFRAME: Timeframe = "1h";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
export const marketDataClient = createMarketDataClient(baseUrl, () => useAuthStore.getState().token);
export const chartingClient = createChartingClient(baseUrl, () => useAuthStore.getState().token);
export const backtestsClient = createBacktestsClient(baseUrl, () => useAuthStore.getState().token);

export type FetchCandles = (params: CandleQueryParams) => Promise<CandleQueryResult>;
// DC-18b: 캔들 조회 포트와 같은 관용(테스트에서 서버 왕복 없이 주입) — 캔들의
// venue/timeframe만 넘기고, 축(현재 visible range)은 이 화면이 start/end로 이미
// 갖고 있으므로 CoverageBadge가 그 값을 그대로 받는다.
export type FetchCoverage = (params: CoverageQueryParams) => Promise<CoverageSpanView[]>;

export interface ChartPageProps {
  fetchCandles?: FetchCandles;
  // DC-18b: 미지정 시 실 서버 클라이언트(marketDataClient.getCandles)를 쓴다 —
  // listInstruments와 동일 관용. 이 화면 테스트는 renderPage가 항상 스텁을 넘긴다
  // (실 네트워크 회피).
  fetchCoverage?: FetchCoverage;
  // CH-6b: CH-8 레이아웃 CRUD 포트 — 테스트에서 서버 왕복 없이 주입한다(fetchCandles와 동일 관용).
  chartingPort?: ChartingPort;
  // CH-17c: CH-17b 지표 템플릿 CRUD 포트 — 같은 관용으로 주입 가능하게 둔다.
  templatesPort?: ChartTemplatesPort;
  // CH-13b: CompareSymbols의 InstrumentView 목록 조회 포트 — 같은 관용으로 주입 가능하게 둔다.
  listInstruments?: typeof marketDataClient.listInstruments;
  // BT-13: 즉시 백테스트 실행 포트 — 같은 관용으로 서버 왕복 없이 주입 가능하게 둔다.
  runQuickBacktest?: RunQuickBacktest;
  // CH-18b: IndicatorParityPanel의 화이트리스트 판정 입력(IND-12 카탈로그). 실
  // 배선(라이브 fetch)이 아직 없다 — 기본값 []는 "아무 지표도 검증 대상 아님"을
  // 정직하게 반영한다(fail-closed, IndicatorParityPanel.tsx 상단 주석 참고).
  indicatorCatalog?: readonly IndicatorCatalogEntry[];
  // CH-18b: 서버 참조 지표 시리즈 포트. 실 IND-1 계산 엔드포인트가 아직 없어
  // 기본값은 IndicatorParityPanel의 자체 기본값(항상 null)을 그대로 쓴다.
  resolveServerIndicatorSeries?: ServerIndicatorSeriesPort;
  now?: Date;
}
