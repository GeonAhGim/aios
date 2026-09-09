// clients/*.ts에 흩어진 문자열 경로의 단일 출처(origin/main 5f7c00b 기준 전수 조사).
// foundation.ts의 "/v1/foundation/..."는 이 표의 /api/v1과는 무관한 별도 네임스페이스라
// v1Path를 아직 비워 둔다(PLT-16 mount_v1 구현 전이라 최종 마운트 경로 미확정). task-2098
// (P6 300줄 상한)로 apiPaths.ts에서 이 등록 표만 분리했다(route는 apiRouteTypes.ts,
// resolvePath는 apiPaths.ts) — task-2337: 같은 이유로 이 표도 다 못 담아
// apiRoutesFoundationOps.ts로 일부를 더 분리했다(API_ROUTES는 여전히 단일 export다).

import { defineApiRoutes, route } from "./apiRouteTypes";
import { FOUNDATION_OPS_ROUTES } from "./apiRoutesFoundationOps";

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

  // task-1325: sessions.ts(task-606)가 GET /auth/sessions·DELETE /auth/sessions/:id를
  // 실제로 호출하도록 구현돼 있었지만 이 표에 등록된 적이 없어 apiPaths↔OpenAPI 가드(§A)의
  // 유령 경로 검사 대상에서 빠져 있었다(가드는 API_ROUTES에 등록된 것만 본다).
  // src/api/routers 목록 재확인 — auth.py에는 register/login/refresh/logout/logout-all/
  // mfa/setup/mfa/verify뿐이고 별도 sessions 라우터 파일이 없다(디렉터리 목록: admin/
  // alerts/auth/device_tokens/exchange_credentials/executions/foundation/health/
  // marketplace/metrics/notifications/portfolio/reports/strategy_builder/suitability/
  // users/wallet.py) — marketData.*와 같은 이유의 유령 경로다. v1Path는 마운트 경로
  // 확정 전이라 null, implemented=false로 sessions.ts가 네트워크 호출 전에 typed
  // 오류로 단락하게 한다.
  "auth.sessions.list": route("/auth/sessions", true, null, false),
  "auth.sessions.revoke": route("/auth/sessions/:sessionId", true, null, false),

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
  // task-1333: §9 PLT-15 금전 라우트 — idempotencyRequired=true.
  "admin.wallet.topupConfirm": route("/admin/wallet/topups/:topupId/confirm", true, undefined, undefined, true),
  "admin.marketplace.platformListings": route("/admin/marketplace/platform-listings", true),
  "admin.approvalRequests.approve": route("/admin/approval-requests/:requestId/approve", true),
  "admin.approvalRequests.reject": route("/admin/approval-requests/:requestId/reject", true),
  "admin.approvalRequests.pending": route("/admin/approval-requests/pending", true),

  // task-1333: §9 PLT-15 금전 라우트 — idempotencyRequired=true.
  "exchange.credentials.base": route("/exchange-credentials", false, undefined, undefined, true),
  "exchange.credentials.item": route("/exchange-credentials/:exchange", false),
  "exchange.credentials.balance": route("/exchange-credentials/:exchange/balance", false),
  "exchange.credentials.capabilities": route("/exchange-credentials/:exchange/capabilities", false),

  // task-1333: §9 PLT-15 금전 라우트(create/start/convertToLive) — idempotencyRequired=true.
  // pause/retire/riskGuard는 PLT-15 대상이 아니다(spec §9 표 참조) — 표식 없음.
  "executions.base": route("/executions", false, undefined, undefined, true),
  "executions.start": route("/executions/:executionId/start", false, undefined, undefined, true),
  "executions.pause": route("/executions/:executionId/pause", false),
  "executions.retire": route("/executions/:executionId/retire", false),
  "executions.convertToLive": route("/executions/:executionId/convert-to-live", false, undefined, undefined, true),
  "executions.riskGuard": route("/executions/:executionId/risk-guard", false),

  "marketplace.listings.base": route("/marketplace/listings", false),
  "marketplace.listings.submitVerification": route("/marketplace/listings/:listingId/submit-verification", false),
  "marketplace.listings.verify": route("/marketplace/listings/:listingId/verify", false),
  // task-1333: §9 PLT-15 금전 라우트 — idempotencyRequired=true.
  "marketplace.listings.purchase": route(
    "/marketplace/listings/:listingId/purchase",
    false,
    undefined,
    undefined,
    true,
  ),
  "marketplace.strategies.get": route("/marketplace/strategies/:strategyId/:version", false),
  "marketplace.listings.reviews": route("/marketplace/listings/:listingId/reviews", false),
  "marketplace.disputes.create": route("/marketplace/disputes", false),

  "notifications.history": route("/notifications/history", false),
  "notifications.preferences": route("/notifications/preferences", false),
  "deviceTokens.register": route("/device-tokens", false),
  "deviceTokens.deactivate": route("/device-tokens/:deviceId", false),

  "portfolio.get": route("/portfolio", false),
  // task-1333: §9 PLT-15 금전 라우트 — idempotencyRequired=true.
  "portfolio.rebalance": route("/portfolio/rebalance", false, undefined, undefined, true),
  "wallet.balance": route("/wallet/balance", false),
  "wallet.topupRequests": route("/wallet/topup-requests", false, undefined, undefined, true),
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

  // task-1309: paper_control.py/trust.py 원본 확인 — mount_v1 미배선(PLT-16
  // 미도달)이라 v1Path는 여전히 null이지만, 두 라우터 모두 처음부터 모든
  // 엔드포인트가 `-> ApiResponse[T]`(ok())로 응답한다(legacy/v1 이관과 무관하게
  // "지금" 이미 봉투). GET(list, POST와 경로 공유)도 동일 라우터 소속이라
  // envelope=true다.
  // task-1333: §9 PLT-15 금전 라우트(paper-control 5개 + trust/consents) —
  // idempotencyRequired=true.
  "foundation.paperDeployments.request": route("/v1/foundation/paper-deployments", true, null, undefined, true),
  "foundation.paperDeployments.start": route(
    "/v1/foundation/paper-deployments/:deploymentId:start",
    true,
    null,
    undefined,
    true,
  ),
  "foundation.paperDeployments.resume": route(
    "/v1/foundation/paper-deployments/:deploymentId:resume",
    true,
    null,
    undefined,
    true,
  ),
  "foundation.paperDeployments.pause": route(
    "/v1/foundation/paper-deployments/:deploymentId:pause",
    true,
    null,
    undefined,
    true,
  ),
  "foundation.paperDeployments.stop": route(
    "/v1/foundation/paper-deployments/:deploymentId:stop",
    true,
    null,
    undefined,
    true,
  ),
  "foundation.trustConsents.accept": route("/v1/foundation/trust/consents", true, null, undefined, true),

  // task-719/824: LA-17(task-624, 7ad6d15) 조회 클라이언트 경로. 등록 당시엔
  // src/api/routers에 market_data 라우터가 없어 v1Path=null·envelope=false로 두었다.
  // task-1376(LA-24): 라우터 실재(src/api/routers/market_data.py) → implemented=true.
  // task-1525: src/api/routers/market_data.py 원문 확인 — `APIRouter(prefix=
  // "/v1/foundation/market-data")`(market_data.py:75), router_registry.py:68
  // `include_router(market_data.router)`(추가 prefix 없음, mount_v1 미경유라 v1Path는
  // 여전히 null). 네 엔드포인트 전부 `-> ApiResponse[...]` + `ok(...)`라 envelope=true:
  //   GET /candles                      :89  → ApiResponse[CandleSeriesView]   :108, ok(view, page=) :136
  //   GET /candles/replay               :139 → ApiResponse[ReplaySeriesView]   :156, ok(view)        :181
  //   GET /instruments                  :184 → ApiResponse[InstrumentListView] :194, ok(view, page=) :208
  //   GET /instruments/{symbol}/aliases :211 → ApiResponse[list[SymbolAliasRef]] :220, ok(aliases)  :236
  // 쿼리(candles): venue·timeframe·start·end 필수, symbol|instrument_id 택1, as_of·
  // adjustment(RAW)·cursor·limit(≤1000). replay는 as_of 필수·cursor/limit 없음.
  // instruments: venue·status·cursor(UUID keyset)·limit(≤200). aliases 경로 세그먼트는
  // UUID(instrument_id) 또는 벤처 심볼(이때 venue 필수) — 프론트는 UUID를 보낸다.
  // 커버리지 밖 span → 409 DATA_COVERAGE_MISSING(error_codes.py:63·:95, market_data.py:125
  // 및 replay의 ReplayIncompleteError → exception_registry_foundation.py:239-240).
  "marketData.candles.get": route("/v1/foundation/market-data/candles", true, null, true),
  "marketData.candles.replay": route("/v1/foundation/market-data/candles/replay", true, null, true),
  "marketData.instruments.list": route("/v1/foundation/market-data/instruments", true, null, true),
  "marketData.instruments.aliases": route(
    "/v1/foundation/market-data/instruments/:instrumentId/aliases",
    true,
    null,
    true,
  ),
  // task-2196(DC-18b): task-2195(DC-18a, ddacfca6)가 실제로 배선한
  // GET /v1/foundation/market-data/coverage — kwargs(instrument_id·venue·
  // timeframe) 필수, no-coverage/no-entitlement는 200+[]다(market_data.py:283-299).
  // 스냅샷(contracts/openapi/v1.json)은 task-2195 병합 이후 재생성되지 않아 아직
  // 이 경로가 없다 — apiPaths.openapi.test.ts의 STALE_SNAPSHOT_WHITELIST에 등재.
  "marketData.coverage.get": route("/v1/foundation/market-data/coverage", true, null, true),

  // task-1524(LB-19): src/api/routers/positions.py 원문 확인 — `APIRouter(prefix=
  // "/v1/positions")`(positions.py:54), router_registry.py:68 `include_router(positions.
  // router)`(추가 prefix 없음). 세 엔드포인트 전부 `-> ApiResponse[...]` + `ok(...)`라
  // envelope=true. GET ""(list, positions.py:69) 쿼리는 account_id·instrument_id(둘 다
  // 옵션 UUID); GET "/nav"(positions.py:87)는 account_id·start_date·end_date 필수;
  // GET "/{position_key}/journal"(positions.py:122)은 cursor(옵션 문자열, 내용은
  // sequence_no)·limit(1~200, 기본 50)이고 next_cursor는 봉투 meta.page에만 실린다.
  // /api/v1 마운트(PLT-16)는 미도달이라 v1Path=null. 스냅샷(f800c1a)에는 아직
  // 없으므로 apiPaths.openapi.test.ts STALE_SNAPSHOT_WHITELIST에 기록했다.
  "positions.list": route("/v1/positions", true, null, true),
  "positions.nav": route("/v1/positions/nav", true, null, true),
  "positions.journal": route("/v1/positions/:positionKey/journal", true, null, true),

  // task-1558(DSL-13a): src/api/routers/scripts.py 원문 확인 — `APIRouter(prefix=
  // "/v1/scripts")`(scripts.py:38), `POST /compile`(:46) `-> ApiResponse[CompileScriptView]`
  // + `ok(...)`(:64), router_registry.py:69 `include_router(scripts.router)`(추가
  // prefix 없음). mount_v1(PLT-16)은 미도달이라 v1Path=null, positions.*·marketData.*와
  // 동일 사유. envelope=true.
  "scripts.compile": route("/v1/scripts/compile", true, null, true),

  // task-1607(BT-13): src/api/routers/backtests.py 원문 확인(BT-10c, task-1619
  // 9ccb238) — `APIRouter(prefix="/v1/backtests")`(backtests.py:62),
  // router_registry.py include_router(추가 prefix 없음). `POST /quick`(:71)
  // `-> ApiResponse[QuickBacktestResultView]` + `ok(...)`(:113) — envelope=true.
  // scripts.*·positions.*와 동일 사유로 mount_v1(PLT-16) 미도달, v1Path=null.
  // 동기 실행·무저장(라우터 docstring decision)이라 idempotencyRequired 없음(false).
  "backtests.quick": route("/v1/backtests/quick", true, null, true),

  // task-1593(CH-8): src/api/routers/charting.py 원문 확인(CH-5, task-1557 06e5560) —
  // `APIRouter(prefix="/v1/foundation/charting")`(charting.py:37), router_registry.py
  // include_router(추가 prefix 없음). 7개 엔드포인트 전부 `-> ApiResponse[...]` + `ok(...)`
  // (POST/GET "/layouts" :40·:56, GET/PATCH "/layouts/{id}" :65·:75, DELETE
  // "/layouts/{id}"(204, 봉투 없음이지만 http.ts가 204를 별도 처리하므로 무관) :93,
  // GET/PUT "/layouts/{id}/drawings" :102·:112) — envelope=true. foundation.* 관용과
  // 동일하게 mount_v1(PLT-16) 미도달이라 v1Path=null. 같은 경로를 공유하는 메서드는
  // 항목 하나로 묶는다(위 주석 "메서드별로 별도 항목을 만들지 않는다"): layouts.base
  // (POST+GET), layouts.item(GET+PATCH+DELETE), layouts.drawings(GET+PUT).
  "charting.layouts.base": route("/v1/foundation/charting/layouts", true, null, true),
  "charting.layouts.item": route("/v1/foundation/charting/layouts/:layoutId", true, null, true),
  "charting.layouts.drawings": route("/v1/foundation/charting/layouts/:layoutId/drawings", true, null, true),

  // task-1904(CH-17b): 같은 charting.py 라우터에 얹은 지표 템플릿 CRUD(src/api/routers/
  // charting.py post_create_indicator_template :146·get_list_indicator_templates :161·
  // get_indicator_template_by_id :170·delete_indicator_template_by_id :181) — 전부
  // `-> ApiResponse[...]` + `ok(...)`(DELETE는 204, http.ts가 별도 처리)라 envelope=true.
  // charting.layouts.*와 동일 사유로 mount_v1(PLT-16) 미도달, v1Path=null. 같은 경로
  // 공유 관용대로 base(POST+GET), item(GET+DELETE, PATCH 없음 — 템플릿은 UNIQUE(tenant_id,
  // name) 제약 위에 생성·삭제만 있고 갱신 엔드포인트가 없다)로 묶는다.
  "charting.indicatorTemplates.base": route("/v1/foundation/charting/indicator-templates", true, null, true),
  "charting.indicatorTemplates.item": route("/v1/foundation/charting/indicator-templates/:templateId", true, null, true),

  // task-1731(CH-11): src/api/routers/indicators.py 원문 확인(IND-12, task-1730
  // 934b8d1) — `APIRouter(prefix="/v1/indicators")`(indicators.py:37), `GET ""`
  // (:54) `-> ApiResponse[IndicatorListView]` + `ok(...)`(:71), router_registry.py
  // include_router(추가 prefix 없음). scripts.*·backtests.*와 동일 사유로
  // mount_v1(PLT-16) 미도달이라 v1Path=null. envelope=true. 스냅샷
  // (contracts/openapi/v1.json)에 "/v1/indicators"가 이미 있고
  // ApiResponse_IndicatorListView_를 참조한다 — task-1730이 이 leaf보다 먼저
  // 머지돼 스냅샷이 이미 갱신돼 있으므로(scripts.compile 등과 달리)
  // STALE_SNAPSHOT_WHITELIST 대상이 아니다.
  "indicators.list": route("/v1/indicators", true, null, true),

  // task-2335(FE-OPS-1): src/api/routers/foundation/risk_gate.py 원문 확인 —
  // `APIRouter(prefix="/v1/foundation/risk-gate")`(risk_gate.py:75), router_registry.py
  // include_router(추가 prefix 없음). 이 리프는 안전 통제(safety control) 조회·해제
  // (deactivate·evaluate-recovery)만 등록한다 — 개통(POST 자가/관리자 activate)·
  // 룰번들 승인/활성화·evaluate 트리거는 decision상 UI가 없어 apiPaths.openapi.test.ts의
  // UNREGISTERED_ROUTE_WHITELIST에 그대로 남는다(후속 리프 2336~2338 소관).
  // GET/POST "/safety-controls"(:97·:106)는 같은 경로를 공유하므로(apiRouteTypes.ts
  // 축약 관용) 한 항목으로 묶는다 — 이 화면은 GET(list)만 쓴다. 세 라우트 전부
  // `-> ApiResponse[...]` + `ok(...)`(:94·:136·:189)라 envelope=true. mount_v1(PLT-16)
  // 미도달이라 v1Path=null(foundation.*와 동일 사유).
  "riskGate.safetyControls.list": route("/v1/foundation/risk-gate/safety-controls", true, null, true),
  "riskGate.safetyControls.deactivate": route(
    "/v1/foundation/risk-gate/safety-controls/:controlId:deactivate",
    true,
    null,
    true,
  ),
  // evaluate-recovery(risk_gate.py:161)는 contracts/openapi/v1.json에 아직 없다(grep
  // 직접 확인 — risk-gate 6경로 중 :evaluate-recovery만 스냅샷에 없음) — 라우터는
  // 실재하므로 GHOST_PATH_WHITELIST(라우터 자체가 없음)가 아니라
  // STALE_SNAPSHOT_WHITELIST(라우터는 있는데 스냅샷이 낡음, CH-17c 선례)로 뺀다.
  "riskGate.safetyControls.evaluateRecovery": route(
    "/v1/foundation/risk-gate/safety-controls/:controlId:evaluate-recovery",
    true,
    null,
    true,
  ),

  // task-2336(FE-OPS-2): src/api/routers/foundation/mandates.py 원문 확인 —
  // `APIRouter(prefix="/v1/foundation/mandates")`(mandates.py:43), router_registry.py
  // include_router(추가 prefix 없음). 7라우트 전부 `-> ApiResponse[...]` + `ok(...)`
  // (:50·:66·:88·:101·:135·:147·:159)라 envelope=true. riskGate.*와 동일 사유로
  // mount_v1(PLT-16) 미도달이라 v1Path=null. contracts/openapi/v1.json에 7경로 전부
  // 실재함을 python으로 직접 확인(paths 키 대조) — STALE_SNAPSHOT_WHITELIST 대상 아님.
  "mandates.status": route("/v1/foundation/mandates/status", true, null, true),
  "mandates.drafts.create": route("/v1/foundation/mandates/drafts", true, null, true),
  "mandates.amendments.propose": route("/v1/foundation/mandates/amendments", true, null, true),
  "mandates.revisions.activate": route(
    "/v1/foundation/mandates/revisions/:revisionId:activate",
    true,
    null,
    true,
  ),
  "mandates.mandate.pause": route("/v1/foundation/mandates/mandate:pause", true, null, true),
  "mandates.mandate.resume": route("/v1/foundation/mandates/mandate:resume", true, null, true),
  "mandates.policy.evaluate": route("/v1/foundation/mandates/policy:evaluate", true, null, true),

  // task-2412(FE-OPS-8): src/api/routers/foundation/validation.py 원문 확인 — POST
  // "/{strategy_id}/{strategy_version}" -> ApiResponse[ValidationResultView] + ok(...).
  // envelope=true, mount_v1(PLT-16) 미도달이라 v1Path=null(foundation.*와 동일 사유).
  "validation.start": route("/v1/foundation/validation-runs/:strategyId/:strategyVersion", true, null, true),
  ...FOUNDATION_OPS_ROUTES,
});
