// L4 platform spec §3.3(ApiResponse 봉투는 "/api/v1 경로에만 적용, 레거시 alias는
// 구형 그대로 반환") + §9 PLT-16(mount_v1)/PLT-17~21(라우터별 봉투 이관).
// 서버가 모든 라우터를 /api/v1 아래로 옮기는 동안, 프론트 clients/*.ts에 흩어진
// 문자열 경로를 이 파일 하나로 모으고 legacy↔v1 전환 스위치(useV1)를 준비한다.
//
// 이 파일은 등록만 한다 — 기본값은 항상 legacy(useV1 미지정 시 false)이므로
// clients/*.ts를 단 한 줄도 바꾸지 않고도 안전하게 머지할 수 있다. 실제 클라이언트
// 배선(this.request 호출부를 resolvePath/requestByRoute로 교체)은 PLT-17~21 순서를
// 따라가는 후속 리프의 몫이다.

export interface ApiRouteDefinition {
  legacyPath: string;
  // 서버가 아직 그 라우터를 /api/v1로 이관하지 않았으면(PLT-17~21 미도달)
  // undefined다 — resolvePath는 이 경우 useV1=true여도 legacy로 폴백한다.
  v1Path?: string;
  // 이 라우트가 "지금"(legacy 경로 기준) ApiResponse 봉투로 응답하는지.
  // task-112(28cf21b)로 auth/users/admin 라우터는 legacy 경로에서도 이미 봉투를
  // 쓰고, 나머지는 PLT-17~21이 각 라우터를 이관할 때 함께 true로 바뀐다.
  // v1Path로 실제 해석된 경우는 스펙상 항상 봉투이므로 resolveEnvelope가
  // 이 값 대신 true를 강제한다.
  envelope: boolean;
}

// 같은 경로를 여러 HTTP 메서드(GET/POST/PUT/...)가 공유하는 경우(예:
// "/executions" GET 목록 + POST 생성) 메서드별로 별도 항목을 만들지 않는다 —
// legacyPath는 "리소스 경로"의 단일 출처이지 "연산"의 단일 출처가 아니다.
// 이 레포의 실제 라우터는 같은 경로를 공유하는 메서드끼리 봉투 여부가 항상
// 동일하므로(라우터 단위로 이관되기 때문) 이 축약이 안전하다.
function route(legacyPath: string, envelope: boolean, v1Path: string | null = `/api/v1${legacyPath}`): ApiRouteDefinition {
  return { legacyPath, envelope, v1Path: v1Path ?? undefined };
}

function defineApiRoutes<T extends Record<string, ApiRouteDefinition>>(routes: T): T {
  const seenLegacyPaths = new Set<string>();
  for (const [name, def] of Object.entries(routes)) {
    if (seenLegacyPaths.has(def.legacyPath)) {
      throw new Error(`apiPaths: legacyPath가 중복 등록되었습니다("${def.legacyPath}", route="${name}")`);
    }
    seenLegacyPaths.add(def.legacyPath);
  }
  return routes;
}

// clients/*.ts에 흩어진 문자열 경로의 단일 출처(origin/main 5f7c00b 기준 전수
// 조사). foundation.ts의 "/v1/foundation/..."는 이 표의 /api/v1과는 무관한
// 별도 네임스페이스라 v1Path를 아직 비워 둔다(PLT-16 mount_v1 구현 전이라
// 최종 마운트 경로가 확정되지 않았다 — 추측 대신 null로 명시).
export const API_ROUTES = defineApiRoutes({
  "auth.register": route("/auth/register", true),
  "auth.login": route("/auth/login", true),
  // task-1324: origin/main 0a68f86에서 확인 — PLT-24(task-1075, e0eb498) 병합으로
  // src/api/routers/auth.py에 실재하는 두 라우트. 둘 다 ApiResponse 봉투를 쓴다
  // (refresh: ApiResponse[TokenPairResponse], logout-all: ApiResponse[dict[str,int]]).
  "auth.refresh": route("/auth/refresh", true),
  "auth.logout": route("/auth/logout", true),
  "auth.logoutAll": route("/auth/logout-all", true),
  "auth.mfaSetup": route("/auth/mfa/setup", true),
  "auth.mfaVerify": route("/auth/mfa/verify", true),
  "auth.me": route("/users/me", true),

  "account.riskAssessment": route("/users/me/risk-assessment", false),
  "account.riskProfile": route("/users/me/risk-profile", false),
  "account.riskProfileHistory": route("/users/me/risk-profile/history", false),
  "account.approvalSettings": route("/users/me/approval-settings", true),
  "account.whitelist": route("/users/me/withdrawal-whitelist", true),
  "account.deletion": route("/users/me/delete", true),
  "account.approvalRequests.list": route("/users/me/approval-requests", true),
  "account.approvalRequests.approve": route("/users/me/approval-requests/:requestId/approve", true),
  "account.approvalRequests.reject": route("/users/me/approval-requests/:requestId/reject", true),

  "admin.verificationQueue": route("/admin/verification-queue", true),
  "admin.disputes.list": route("/admin/disputes", true),
  "admin.disputes.get": route("/admin/disputes/:disputeId", true),
  "admin.disputes.resolve": route("/admin/disputes/:disputeId/resolve", true),
  "admin.users.list": route("/admin/users", true),
  "admin.users.status": route("/admin/users/:userId/status", true),
  "admin.users.suspendSeller": route("/admin/users/:userId/suspend-seller", true),
  "admin.wallet.topupsPending": route("/admin/wallet/topups/pending", true),
  "admin.wallet.topupConfirm": route("/admin/wallet/topups/:topupId/confirm", true),
  "admin.marketplace.platformListings": route("/admin/marketplace/platform-listings", true),
  "admin.approvalRequests.approve": route("/admin/approval-requests/:requestId/approve", true),
  "admin.approvalRequests.reject": route("/admin/approval-requests/:requestId/reject", true),
  "admin.approvalRequests.pending": route("/admin/approval-requests/pending", true),

  "exchange.credentials.base": route("/exchange-credentials", false),
  "exchange.credentials.item": route("/exchange-credentials/:exchange", false),
  "exchange.credentials.balance": route("/exchange-credentials/:exchange/balance", false),
  "exchange.credentials.capabilities": route("/exchange-credentials/:exchange/capabilities", false),

  "executions.base": route("/executions", false),
  "executions.start": route("/executions/:executionId/start", false),
  "executions.pause": route("/executions/:executionId/pause", false),
  "executions.retire": route("/executions/:executionId/retire", false),
  "executions.convertToLive": route("/executions/:executionId/convert-to-live", false),
  "executions.riskGuard": route("/executions/:executionId/risk-guard", false),

  "marketplace.listings.base": route("/marketplace/listings", false),
  "marketplace.listings.submitVerification": route("/marketplace/listings/:listingId/submit-verification", false),
  "marketplace.listings.verify": route("/marketplace/listings/:listingId/verify", false),
  "marketplace.listings.purchase": route("/marketplace/listings/:listingId/purchase", false),
  "marketplace.strategies.get": route("/marketplace/strategies/:strategyId/:version", false),
  "marketplace.listings.reviews": route("/marketplace/listings/:listingId/reviews", false),
  "marketplace.disputes.create": route("/marketplace/disputes", false),

  "notifications.history": route("/notifications/history", false),
  "notifications.preferences": route("/notifications/preferences", false),
  "deviceTokens.register": route("/device-tokens", false),
  "deviceTokens.deactivate": route("/device-tokens/:deviceId", false),

  "portfolio.get": route("/portfolio", false),
  "portfolio.rebalance": route("/portfolio/rebalance", false),
  "wallet.balance": route("/wallet/balance", false),
  "wallet.topupRequests": route("/wallet/topup-requests", false),
  "alerts.base": route("/alerts", false),
  "alerts.cancel": route("/alerts/:alertId/cancel", false),
  "reports.generate": route("/reports", false),

  "strategyBuilder.indicators.list": route("/strategy-builder/indicators", false),
  "strategyBuilder.strategies.base": route("/strategy-builder/strategies", false),
  "strategyBuilder.candles": route("/strategy-builder/candles", false),
  "strategyBuilder.indicators.compute": route("/strategy-builder/indicators/:name/compute", false),
  "strategyBuilder.strategies.get": route("/strategy-builder/strategies/:strategyId/:version", false),
  "strategyBuilder.preview": route("/strategy-builder/preview", false),
  "strategyBuilder.wizard": route("/strategy-builder/wizard", false),
  "strategyBuilder.generateFromPrompt": route("/strategy-builder/generate-from-prompt", false),

  "foundation.paperDeployments.request": route("/v1/foundation/paper-deployments", false, null),
  "foundation.paperDeployments.start": route("/v1/foundation/paper-deployments/:deploymentId:start", false, null),
  "foundation.paperDeployments.resume": route("/v1/foundation/paper-deployments/:deploymentId:resume", false, null),
  "foundation.paperDeployments.pause": route("/v1/foundation/paper-deployments/:deploymentId:pause", false, null),
  "foundation.paperDeployments.stop": route("/v1/foundation/paper-deployments/:deploymentId:stop", false, null),
  "foundation.trustConsents.accept": route("/v1/foundation/trust/consents", false, null),

  // task-719: LA-17(task-624, 7ad6d15) application/get_candles·replay_candles의 조회
  // 클라이언트. src/api/routers에는 아직 market_data 라우터가 없다(PLT-16 mount_v1
  // 미도달 + 이 이름의 라우터 자체가 아직 없음, foundation.* 이관 전과 동일 상황) —
  // 그래서 foundation.* 항목과 동일하게 v1Path=null로 legacy만 등록한다. 실제 마운트
  // 경로가 확정되면(라우터 파일 확인 후) 이 값만 고친다.
  "marketData.candles.get": route("/v1/foundation/market-data/candles", false, null),
  "marketData.candles.replay": route("/v1/foundation/market-data/candles/replay", false, null),

  // task-824: §3.1 InstrumentView 목록·별칭 조회. LA-9(ports/reference_repository.py)에는
  // get_instrument(단건)만 있고 목록·별칭 조회 메서드가 아직 없다 — marketData.candles.*와
  // 같은 이유(라우터 자체가 아직 없음)로 v1Path=null·legacy만 등록해둔다. 실제 라우터가
  // 생기면(LA-9 확장 후) 이 값만 고친다.
  "marketData.instruments.list": route("/v1/foundation/market-data/instruments", false, null),
  "marketData.instruments.aliases": route(
    "/v1/foundation/market-data/instruments/:instrumentId/aliases",
    false,
    null,
  ),
});

export type ApiRouteName = keyof typeof API_ROUTES;

export interface ResolvePathOptions {
  useV1?: boolean;
}

function getRouteDefinition(route: ApiRouteName): ApiRouteDefinition {
  const def = API_ROUTES[route];
  if (!def) {
    throw new Error(`apiPaths: 미등록 route입니다("${route}")`);
  }
  return def;
}

// v1Path가 없는 라우트는 useV1=true를 줘도 legacy로 폴백한다 — 서버 이관이
// 그 라우터에 아직 도달하지 않았다는 뜻이라 v1 경로 자체가 존재하지 않는다.
export function resolvePath(route: ApiRouteName, options: ResolvePathOptions = {}): string {
  const def = getRouteDefinition(route);
  if (options.useV1 && def.v1Path) return def.v1Path;
  return def.legacyPath;
}

// v1로 실제 해석됐을 때는 스펙상 항상 봉투가 적용된다(§3.3) — legacy로 폴백된
// 경우(useV1 미지정 포함)에만 라우트별 현재값을 쓴다.
export function resolveEnvelope(route: ApiRouteName, options: ResolvePathOptions = {}): boolean {
  const def = getRouteDefinition(route);
  if (options.useV1 && def.v1Path) return true;
  return def.envelope;
}

// 테스트 전용 export — 실제 clients/*.ts는 API_ROUTES를 통해서만 등록한다.
export { defineApiRoutes };
