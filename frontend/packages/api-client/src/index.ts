export { AiosApiClient, ApiError } from "./client";
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
export { createSessionsClient } from "./sessions";
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
// 멱등 클라이언트. AiosApiClient 합성(client.ts)에는 아직 배선되지 않았다 —
// 후속 리프가 withFoundation을 composed client에 얹는다.
export { withFoundation } from "./clients/foundation";
export type {
  PaperDeploymentState,
  PaperDeploymentView,
  RequestPaperDeploymentBody,
  ConsentState,
  ConsentDecision,
  AcceptTrustConsentBody,
} from "./clients/foundation";
// task-605: §3.3 API 경로 레지스트리 — clients/*.ts에 흩어진 문자열 경로의
// 단일 출처 + legacy/v1 전환 스위치. 기본값은 legacy이며 아직 어떤
// clients/*.ts도 이 스위치를 쓰지 않는다(레지스트리 준비 단계).
export { API_ROUTES, resolvePath, resolveEnvelope } from "./apiPaths";
export type { ApiRouteDefinition, ApiRouteName, ResolvePathOptions } from "./apiPaths";
// task-617: §3.5 멤버십 관리 클라이언트(grant/suspend/revoke). AiosApiClient 합성
// (client.ts)에는 아직 배선되지 않았다(PLT-29 서버 라우터 미구현) — 후속 리프 소관.
export { createMembershipsClient, MembershipParseError } from "./memberships";
export type { MembershipsClient, GrantMembershipBody } from "./memberships";
