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
  // task-4024(FE-OPS-7a): src/api/routers/admin.py:84 GET /admin/audit-log 원문 확인 —
  // `-> ApiResponse[AuditLogPage]` + `ok(...)`(:94/:102)라 envelope=true(스냅샷 컴포넌트
  // ApiResponse_AuditLogPage_로 재확인, python으로 contracts/openapi/v1.json 직접 대조).
  // PLT-35-fix(task-3850)가 require_break_glass("tenant_read")를 얹어 X-Break-Glass-Grant
  // 헤더(UUID)가 필수다 — clients/admin.ts의 listAuditLog가 호출자에게서 grant id를
  // 받아 헤더로 흘려보낸다. 다른 admin.* 관용대로 v1Path는 명시하지 않는다(기본값
  // /api/v1/admin/audit-log).
  "admin.auditLog": route("/admin/audit-log", true),
  // task-4024(FE-OPS-7a): src/api/routers/foundation/ledger_admin.py:53 POST
  // /admin/ledger/payouts/{batch_id}/paid 원문 확인 — `-> ApiResponse[PayoutBatchView]`
  // + `ok(...)`(:53/:69)라 envelope=true(스냅샷 ApiResponse_PayoutBatchView_로 재확인).
  // 위 admin.auditLog와 동일하게 require_break_glass("tenant_read") 헤더가 필수다.
  // idempotencyRequired는 표시하지 않는다 — mark_payout_paid(LC-15a)는 배치 상태
  // (SCHEDULED/RELEASED→PAID) 조건부 UPDATE로 재확정을 막고(ConcurrencyConflictError),
  // 라우터 자체도 require_idempotency_key를 쓰지 않는다(admin.py의 wallet 확정 라우트와
  // 달리 import조차 없음) — spec §9 PLT-15 금전 라우트 표(L4_platform_observability_
  // tenancy_api_v1.0.md 라인 438)에도 이 라우트가 없다. idempotencyScan.test.ts의
  // PLT15_MONEY_ROUTES 양방향 대조를 깨지 않으려면 이 표식을 붙이지 않아야 한다.
  "admin.ledger.payoutsMarkPaid": route("/admin/ledger/payouts/:batchId/paid", true),

  // task-1333: §9 PLT-15 금전 라우트 — idempotencyRequired=true.
  "exchange.credentials.base": route("/exchange-credentials", false, undefined, undefined, true),
  "exchange.credentials.item": route("/exchange-credentials/:exchange", false),
  "exchange.credentials.balance": route("/exchange-credentials/:exchange/balance", false),
  // task-4001(FE-OPS-10a): src/api/routers/exchange_credentials.py:97-104 get_positions는
  // list[Position]을 그대로 반환한다(ApiResponse 봉투 아님) — balance/capabilities와
  // 동일하게 envelope=false.
  "exchange.credentials.positions": route("/exchange-credentials/:exchange/positions", false),
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

  // task-7774(BT-18): SweepResultsPage.tsx가 그리드 스윕 결과(히트맵·안정성 표면·
  // 재현 키)를 그리는 실행 엔드포인트. src/api/routers/backtests.py의
  // `POST /v1/backtests/sweep`(sweep_backtest_endpoint) -> `ApiResponse[SweepResultView]`,
  // ok() 봉투 — backtests.quick과 동일한 envelope=true 관용.
  "backtests.sweep": route("/v1/backtests/sweep", true, null, true),

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
  // (deactivate·evaluate-recovery)만 등록했다 — 개통(POST 자가/관리자 activate)·
  // 룰번들 승인/활성화·evaluate 트리거는 task-5808(FE-OPS-9)이 아래에 추가로 등록한다.
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
  // task-3850(PLT-35-fix): require_break_glass 배선과 함께 contracts/openapi/v1.json을
  // 갱신해 이제 스냅샷에 실재한다 — STALE_SNAPSHOT_WHITELIST에서 뺐다(apiPaths.openapi.test.ts).
  // `:controlId:evaluate-recovery`(camelCase+콜론 복합 세그먼트) vs 서버의
  // `{control_id}:evaluate-recovery`(snake_case) 표기 차이는 scripts/check_consistency.py의
  // 크루드 정규화가 구분 못 하는 별개의 오탐이라 그쪽 `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST`에서 다룬다.
  "riskGate.safetyControls.evaluateRecovery": route(
    "/v1/foundation/risk-gate/safety-controls/:controlId:evaluate-recovery",
    true,
    null,
    true,
  ),

  // task-5808(FE-OPS-9): task-2335가 decision상 UI 없음으로 미뤄뒀던 개통(admin
  // activate)·룰번들 승인/활성화·evaluate 트리거 4건. src/api/routers/foundation/
  // risk_gate.py 원문 확인 — POST /admin/safety-controls(:212)·POST /evaluate(:86)·
  // POST /rule-bundles/{bundle_id}:approve(:249)·POST /rule-bundles/{bundle_id}:activate
  // (:268), 전부 `-> ApiResponse[...]`(contracts/openapi/v1.json에서 python으로 4경로
  // 전부 ApiResponse_SafetyControlView_/ApiResponse_RiskEvaluationView_/
  // ApiResponse_RiskRuleBundle_ 참조 직접 확인)라 envelope=true. mount_v1(PLT-16)
  // 미도달이라 v1Path=null(riskGate.safetyControls.*와 동일 사유).
  "riskGate.safetyControls.activate": route("/v1/foundation/risk-gate/admin/safety-controls", true, null, true),
  "riskGate.evaluate": route("/v1/foundation/risk-gate/evaluate", true, null, true),
  "riskGate.ruleBundles.approve": route(
    "/v1/foundation/risk-gate/rule-bundles/:bundleId:approve",
    true,
    null,
    true,
  ),
  "riskGate.ruleBundles.activate": route(
    "/v1/foundation/risk-gate/rule-bundles/:bundleId:activate",
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

  // task-2668(CM-18): src/api/routers/foundation/compliance.py 원문 확인(task-2618,
  // 098380f7) — `APIRouter(prefix="/v1/foundation/compliance")`, GET
  // "/decisions/{decision_id}" -> ApiResponse[ComplianceDecisionView] + ok(...)
  // (compliance.py:57-63). envelope=true, mount_v1(PLT-16) 미도달이라 v1Path=null
  // (mandates.*와 동일 사유). contracts/openapi/v1.json은 task-2618 병합 이후
  // 재생성된 적이 없어 이 경로가 아직 없다(grep 직접 확인) — 라우터 자체는
  // 실재하므로 GHOST_PATH_WHITELIST가 아니라 STALE_SNAPSHOT_WHITELIST
  // (riskGate.safetyControls.evaluateRecovery와 동일 처리, apiPaths.openapi.test.ts)로 뺀다.
  "compliance.decisions.get": route(
    "/v1/foundation/compliance/decisions/:decisionId",
    true,
    null,
    true,
  ),

  // task-2412(FE-OPS-8): src/api/routers/foundation/validation.py 원문 확인 — POST
  // "/{strategy_id}/{strategy_version}" -> ApiResponse[ValidationResultView] + ok(...).
  // envelope=true, mount_v1(PLT-16) 미도달이라 v1Path=null(foundation.*와 동일 사유).
  "validation.start": route("/v1/foundation/validation-runs/:strategyId/:strategyVersion", true, null, true),

  // task-2699(UX-15): FollowPage.tsx(팔로우 관리·성과 비교)는 spec
  // L4_product_experience_and_discovery_v1.0.md §2.4/UX-13/UX-14가 정의하는
  // `src/foundation/follow/` 모듈을 앞서가는 선행 프론트다 — UX-13(contracts/
  // mirror_rules)·UX-14(mirror_signal)·이를 감싸는 API 라우터
  // (src/api/routers/follow.py) 모두 아직 없다(src/api/routers 디렉터리에
  // follow.py 부재, src/foundation/follow 디렉터리 자체가 없음 — grep으로 직접
  // 확인). auth.sessions.*(task-1325)·backtests.sweep(task-2428)과 동일한 유령
  // 경로 사유로 4개 라우트 모두 implemented=false 등록 — FollowPage.tsx는 라우터가
  // 생기기 전까지 네트워크 호출 대신 FollowRouteNotImplementedError(typed)로
  // 단락한다. v1Path는 마운트 경로 확정 전이라 null. apiPaths.openapi.test.ts
  // GHOST_PATH_WHITELIST에도 함께 추가할 것.
  // GET(list)+POST(create)는 같은 리소스 경로를 공유한다(marketplace.listings.base와
  // 동일 축약 관용, apiRouteTypes.ts 주석 참조) — 항목 하나로 묶는다.
  "follow.subscriptions.base": route("/v1/foundation/follow/subscriptions", true, null, false),
  "follow.subscriptions.cancel": route(
    "/v1/foundation/follow/subscriptions/:subscriptionId",
    true,
    null,
    false,
  ),
  "follow.subscriptions.performance": route(
    "/v1/foundation/follow/subscriptions/:subscriptionId/performance",
    true,
    null,
    false,
  ),

  // task-2657(AI-22): AiStudioPage.tsx(공급자 설정·토큰·제안 목록·실험 비교·승격
  // 버튼 확인 흐름)는 spec L4_ai_research_strategy_factory_v1.0.md §2.1/§2.2/§2.3/
  // §2.4가 정의하는 gateway·providers·factory·experiments 모듈을 앞서가는 선행
  // 프론트다 — 그 모듈들과 이를 감싸는 API 라우터(src/api/routers/ai.py, AI-17)
  // 모두 아직 없다(src/api/routers 디렉터리에 ai.py 부재, src/foundation/ai 디렉터리
  // 자체가 없음 — grep으로 직접 확인). follow.subscriptions.*(task-2699)와 동일한
  // 유령 경로 사유로 8개 라우트 모두 implemented=false 등록 — AiStudioPage.tsx는
  // 라우터가 생기기 전까지 네트워크 호출 대신 AiRouteNotImplementedError(typed)로
  // 단락한다. v1Path는 마운트 경로 확정 전이라 null. apiPaths.openapi.test.ts
  // GHOST_PATH_WHITELIST에도 함께 추가할 것.
  "ai.providers.base": route("/v1/ai/providers", true, null, false),
  "ai.providers.item": route("/v1/ai/providers/:provider", true, null, false),
  // GET(list)+POST(issue)는 같은 리소스 경로를 공유한다(follow.subscriptions.base와
  // 동일 축약 관용).
  // task-4922: src/api/routers/ai.py가 실재하게 됐다(GET/POST /v1/ai/tokens,
  // POST /v1/ai/tokens/{token_id}:revoke, GET /v1/ai/proposals 모두
  // contracts/openapi/v1.json에 ApiResponse_* 봉투로 실재 — python으로 paths 키
  // 직접 확인) — implemented=true로 바꾸고 apiPaths.openapi.test.ts의
  // GHOST_PATH_WHITELIST에서 이 3건을 제거한다. providers.*·proposals.promote*·
  // experiments.base는 스냅샷에 여전히 없어(ai.py에 그 엔드포인트 자체가 없음)
  // 유령 경로로 남는다.
  "ai.tokens.base": route("/v1/ai/tokens", true, null, true),
  "ai.tokens.revoke": route("/v1/ai/tokens/:tokenId:revoke", true, null, true),
  "ai.proposals.base": route("/v1/ai/proposals", true, null, true),
  "ai.proposals.promoteTicket": route("/v1/ai/proposals/:proposalId:promote-ticket", true, null, false),
  "ai.proposals.promote": route("/v1/ai/proposals/:proposalId:promote", true, null, false),
  "ai.experiments.base": route("/v1/ai/experiments", true, null, false),

  // task-7773(UX-8): src/api/routers/screener.py가 실재하게 됐다(POST
  // /v1/foundation/screener/run, contracts/openapi/v1.json에 실재 — python으로
  // paths 키 직접 확인, router_registry.py에 include_router 배선 완료) —
  // implemented=true로 바꾼다. apiPaths.openapi.test.ts의 GHOST_PATH_WHITELIST
  // 에서도 이 항목을 제거했다. v1Path는 mount_v1이 아직 배선되지 않아(PLT-16이
  // PLT-17~21로 미룸, 다른 라우트와 동일) 여전히 null이다.
  "screener.run": route("/v1/foundation/screener/run", true, null, true),

  // task-2696(UX-12): WhatIfPanel.tsx·RebalancePage.tsx는 spec
  // L4_product_experience_and_discovery_v1.0.md §2.3/UX-9/UX-10/UX-11이 정의하는
  // `src/foundation/whatif/` 모듈을 앞서가는 선행 프론트다 — domain/impact.py
  // (UX-9)·application/preview_order.py(UX-10)·application/rebalance_plan.py
  // (UX-11)·이를 감싸는 API 라우터(src/api/routers/whatif.py) 모두 아직 없다
  // (src/api/routers 디렉터리에 whatif.py 부재, src/foundation/whatif 디렉터리
  // 자체가 없음 — grep으로 직접 확인). screener.run(task-2692)과 동일한 유령
  // 경로 사유로 implemented=false 등록 — WhatIfPanel·RebalancePage는 라우터가
  // 생기기 전까지 네트워크 호출 대신 WhatIfRouteNotImplementedError(typed)로
  // 단락한다. v1Path는 마운트 경로 확정 전이라 null. apiPaths.openapi.test.ts
  // GHOST_PATH_WHITELIST에도 함께 추가할 것.
  "whatif.previewOrder": route("/v1/foundation/whatif/preview-order", true, null, false),
  "whatif.rebalancePlan": route("/v1/foundation/whatif/rebalance-plan", true, null, false),

  // task-2718(RD-17): ResearchPage.tsx(검색·소스 상태·종목 연결 표시)는 spec
  // L4_research_data_and_market_ecosystem_v1.0.md §2.1/RD-2~RD-5가 정의하는
  // `src/foundation/research_data/` 모듈을 앞서가는 선행 프론트다 —
  // application/query.py(RD-7, as_of PIT 필터)·entitlement 연동 + 이를 감싸는
  // API 라우터(src/api/routers/research_data.py, RD-8) 모두 아직 없다
  // (src/api/routers 디렉터리에 research_data.py 부재 — grep으로 직접 확인;
  // src/foundation/research_data에는 contracts/domain/adapters/application만
  // 있고 API 라우터가 없다). screener.run(task-2692)·whatif.*(task-2696)와
  // 동일한 유령 경로 사유로 2개 라우트 모두 implemented=false 등록 —
  // ResearchPage.tsx는 라우터가 생기기 전까지 네트워크 호출 대신
  // ResearchDataRouteNotImplementedError(typed)로 단락한다. v1Path는 마운트
  // 경로 확정 전이라 null. apiPaths.openapi.test.ts GHOST_PATH_WHITELIST에도
  // 함께 추가할 것.
  "researchData.search": route("/v1/foundation/research-data/search", true, null, true),
  "researchData.sources.list": route("/v1/foundation/research-data/sources", true, null, true),

  // task-5998(SIG-6): SignalSourcesPage.tsx(시크릿 발급·회전·최근 수신 로그)는 spec
  // L4_analytics_authoring_backtest_marketplace_v1.0.md §9.1(source line 264)이
  // 정의하는 SIG-1~5(`src/foundation/signals/`, `src/api/routers/signals.py`,
  // PLT-33 시크릿 회전)를 앞서가는 선행 프론트다 — 그 모듈·라우터 모두 아직 없다
  // (src/foundation 디렉터리에 signals 부재, src/api/routers 디렉터리에 signals.py
  // 부재 — grep으로 직접 확인). follow.subscriptions.*(task-2699)·screener.run
  // (task-2692)과 동일한 유령 경로 사유로 4개 라우트 모두 implemented=false
  // 등록 — SignalSourcesPage.tsx는 라우터가 생기기 전까지 네트워크 호출 대신
  // SignalsRouteNotImplementedError(typed)로 단락한다. v1Path는 마운트 경로
  // 확정 전이라 null. apiPaths.openapi.test.ts GHOST_PATH_WHITELIST에도 함께
  // 추가할 것.
  // GET(list)+POST(issue)는 같은 리소스 경로를 공유한다(follow.subscriptions.base와
  // 동일 축약 관용).
  "signals.sources.base": route("/v1/foundation/signals/sources", true, null, false),
  "signals.sources.rotate": route("/v1/foundation/signals/sources/:sourceId:rotate", true, null, false),
  "signals.sources.disable": route("/v1/foundation/signals/sources/:sourceId:disable", true, null, false),
  "signals.sources.receipts": route("/v1/foundation/signals/sources/:sourceId/receipts", true, null, false),

  ...FOUNDATION_OPS_ROUTES,
});
