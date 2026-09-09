export { AiosApiClient, ApiError, buildApiError } from "./client";
export { keysToCamel, keysToSnake } from "./caseConvert";
export { unwrap, EnvelopeFormatError, deriveFreshness } from "./envelope";
export { configureUnauthorizedHandler, resetUnauthorizedGuard, configureTenantHeadersProvider } from "./http";
export type { UnauthorizedHandler, TenantHeadersProvider } from "./http";
export { configureTokenRefreshHandler, refreshAccessToken } from "./tokenRefresh";
export type { TokenRefreshHandler } from "./tokenRefresh";
export { configureMfaStepUpHandler, requestMfaStepUp } from "./mfaStepUp";
export type { MfaStepUpHandler } from "./mfaStepUp";
export { createLogoutClient } from "./logout";
export type { LogoutClient, LogoutClientOptions, LogoutTokenStore } from "./logout";
export { createSessionsClient, SessionsRouteNotImplementedError } from "./sessions";
export type { SessionsClient, SessionsClientOptions } from "./sessions";
export { createTenantStore, isValidTenantId } from "./tenantContext";
export type { TenantStore, TenantMismatchFallback, TenantKind, MembershipRole } from "./tenantContext";
export { parseReadiness, summarizeReadiness } from "./readiness";
export type {
  CheckResult,
  ReadinessReport,
  ParsedReadiness,
  FailedCheck,
  ReadinessSummary,
} from "./readiness";
export { newRequestId, isValidRequestId, requestIdHeaders } from "./requestId";
export type { EnvelopeWithMeta } from "./http";
export type {
  ApiResponsePageMeta,
  ApiResponseMeta,
  ApiErrorBody,
  EnvelopeResult,
  Freshness,
  FreshnessKind,
  FreshnessOk,
  FreshnessFuture,
  FreshnessUnavailable,
  DeriveFreshnessOptions,
} from "./envelope";
// task-493: §3.7 PLT-15 잔여 라우트(paper-deployments 5개 + trust/consents)
// 멱등 클라이언트. task-1309가 AiosApiClient 합성(client.ts)에 withFoundation을
// 얹었다.
export { withFoundation } from "./clients/foundation";
export type {
  PaperDeploymentState,
  PaperDeploymentView,
  PaperDeploymentListResponse,
  RequestPaperDeploymentBody,
  ConsentState,
  ConsentDecision,
  AcceptTrustConsentBody,
} from "./clients/foundation";
// task-605: §3.3 API 경로 레지스트리 — clients/*.ts에 흩어진 문자열 경로의
// 단일 출처 + legacy/v1 전환 스위치. 기본값은 legacy이며 아직 어떤
// clients/*.ts도 이 스위치를 쓰지 않는다(레지스트리 준비 단계).
export { API_ROUTES, resolvePath, resolveEnvelope, isRouteImplemented } from "./apiPaths";
export type { ApiRouteDefinition, ApiRouteName, ResolvePathOptions } from "./apiPaths";
// task-617: §3.5 멤버십 관리 클라이언트(grant/suspend/revoke). AiosApiClient 합성
// (client.ts)에는 아직 배선되지 않았다(PLT-29 서버 라우터 미구현) — 후속 리프 소관.
export { createMembershipsClient, MembershipParseError } from "./memberships";
export type { MembershipsClient, GrantMembershipBody } from "./memberships";
// task-719: §3.1 (A) market_data 조회 클라이언트(get_candles/replay_candles).
// AiosApiClient 합성(client.ts)에는 아직 배선되지 않았다(서버 라우터 미마운트) —
// 후속 리프 소관.
export { createMarketDataClient } from "./clients/marketData";
export type {
  MarketDataClient,
  CandleQueryParams,
  ReplayQueryParams,
  CandleQueryResult,
  InstrumentListParams,
  InstrumentListResult,
  CoverageQueryParams,
  CoverageSpanView,
  CoverageQualityGrade,
} from "./clients/marketData";
// task-1524(LB-19): §3.2 (B) positions 조회 클라이언트(list/journal/nav). 서버 라우터
// src/api/routers/positions.py 실재 — AiosApiClient 합성에는 얹지 않고(marketData와
// 동일) 화면이 createPositionsClient로 직접 만든다(테스트 주입 가능).
export { createPositionsClient } from "./clients/positions";
export type {
  PositionsClient,
  PositionListParams,
  PositionListResult,
  PositionJournalParams,
  PositionJournalResult,
  NavSeriesParams,
  NavSeriesResult,
} from "./clients/positions";
// task-1593(CH-8): §9.6 CH-5 차트 레이아웃·드로잉 CRUD 클라이언트(positions.ts와 동일
// 관용 — AiosApiClient 합성에는 얹지 않고 화면이 createChartingClient로 직접 만든다).
export { createChartingClient } from "./clients/charting";
export type {
  ChartingClient,
  ChartLayoutRecord,
  DrawingsDocumentRecord,
  CreateChartLayoutInput,
  UpdateChartLayoutInput,
  PutDrawingsInput,
  ChartIndicatorTemplateRecord,
  CreateChartIndicatorTemplateInput,
} from "./clients/charting";
// task-1607(BT-13): §9.5 BT-10 quick_backtest 클라이언트(charting.ts와 동일 관용 —
// AiosApiClient 합성에는 얹지 않고 화면이 createBacktestsClient로 직접 만든다).
// task-2428(BT-18): sweep 결과 조회(런타임에는 유령 경로 — SweepRouteNotImplementedError
// 참조) 타입·에러도 같은 파일에서 재수출한다.
export { createBacktestsClient, SweepRouteNotImplementedError } from "./clients/backtests";
export type {
  BacktestsClient,
  BacktestSlippageModel,
  BacktestCommissionInput,
  BacktestConfigV2Input,
  QuickBacktestRequestInput,
  BacktestFillView,
  QuickBacktestResultView,
  SweepAxisInput,
  SweepComboInput,
  SweepRequestInput,
  SweepPointResultView,
  SweepStabilityView,
  SweepResultView,
} from "./clients/backtests";
// task-1731(CH-11): §9.11 IND-12 지표 카탈로그 조회 클라이언트(positions.ts와 동일
// 관용 — AiosApiClient 합성에는 얹지 않고 chart-engine plugins/indicatorPlugin.ts의
// IndicatorCatalogPort로 화면이 직접 주입한다).
export { createIndicatorsClient } from "./clients/indicators";
export type { IndicatorCatalogItem, IndicatorsClient, IndicatorTier, ListIndicatorsParams, ListIndicatorsResult } from "./clients/indicators";
