import { describe, expect, it } from "vitest";
import { API_ROUTES, defineApiRoutes, resolveEnvelope, resolvePath, type ApiRouteName } from "./apiPaths";

// task-605: §3.3 API 경로 레지스트리. 기본값(useV1 미지정)은 항상 legacy이므로
// 여기서 실제 호출 경로가 바뀌었는지가 아니라, 스위치 자체가 규칙대로
// 동작하는지만 검증한다.
describe("apiPaths — resolvePath", () => {
  it("useV1을 지정하지 않으면 legacyPath를 돌려준다", () => {
    expect(resolvePath("auth.login")).toBe(API_ROUTES["auth.login"].legacyPath);
  });

  it("useV1=false를 명시해도 legacyPath를 돌려준다", () => {
    expect(resolvePath("auth.login", { useV1: false })).toBe("/auth/login");
  });

  it("v1Path가 있는 라우트는 useV1=true면 v1Path를 돌려준다", () => {
    expect(resolvePath("auth.login", { useV1: true })).toBe("/api/v1/auth/login");
  });

  it("v1Path가 없는 라우트는 useV1=true를 줘도 legacy로 폴백한다", () => {
    const def = API_ROUTES["foundation.trustConsents.accept"];
    expect(def.v1Path).toBeUndefined();
    expect(resolvePath("foundation.trustConsents.accept", { useV1: true })).toBe(def.legacyPath);
  });

  it("미등록 route를 요청하면 throw한다", () => {
    expect(() => resolvePath("not.a.registered.route" as ApiRouteName)).toThrow(/미등록 route/);
  });
});

describe("apiPaths — resolveEnvelope", () => {
  it("legacy로 해석되면 라우트별 현재값을 그대로 돌려준다(false)", () => {
    expect(resolveEnvelope("executions.base")).toBe(false);
  });

  it("legacy로 해석되면 라우트별 현재값을 그대로 돌려준다(true)", () => {
    expect(resolveEnvelope("auth.login")).toBe(true);
  });

  it("v1로 실제 해석되면 legacy 값이 false여도 항상 true다(§3.3)", () => {
    expect(API_ROUTES["executions.base"].envelope).toBe(false);
    expect(API_ROUTES["executions.base"].v1Path).toBe("/api/v1/executions");
    // v1Path가 있는 라우트는 legacy envelope 값과 무관하게 v1 해석 시 true다.
    expect(resolveEnvelope("executions.base", { useV1: true })).toBe(true);

    // v1Path가 아직 없는 라우트는 useV1=true를 줘도 legacy로 폴백하므로
    // envelope도 그 라우트의 legacy 값(false) 그대로다.
    expect(API_ROUTES["foundation.trustConsents.accept"].v1Path).toBeUndefined();
    expect(resolveEnvelope("foundation.trustConsents.accept", { useV1: true })).toBe(false);
  });

  it("미등록 route를 요청하면 throw한다", () => {
    expect(() => resolveEnvelope("not.a.registered.route" as ApiRouteName)).toThrow(/미등록 route/);
  });
});

describe("apiPaths — defineApiRoutes", () => {
  it("legacyPath가 중복 등록되면 throw한다", () => {
    expect(() =>
      defineApiRoutes({
        a: { legacyPath: "/dup", envelope: false },
        b: { legacyPath: "/dup", envelope: true },
      }),
    ).toThrow(/중복 등록/);
  });

  it("legacyPath가 서로 다르면(같은 리소스를 여러 메서드가 공유해도 항목은 하나) 문제없이 등록된다", () => {
    expect(() =>
      defineApiRoutes({
        a: { legacyPath: "/x", envelope: false },
        b: { legacyPath: "/y", envelope: false },
      }),
    ).not.toThrow();
  });

  it("실제 레지스트리(API_ROUTES)는 이미 로드 시점에 중복 검증을 통과했다", () => {
    const legacyPaths = Object.values(API_ROUTES).map((def) => def.legacyPath);
    expect(new Set(legacyPaths).size).toBe(legacyPaths.length);
  });
});

// task-840: clients/marketplace.ts·executions.ts·portfolio.ts가 하드코딩 문자열
// 대신 resolvePath(route)를 경유하도록 바뀌었다. 이 라우트들의 legacyPath가
// 실수로 바뀌면 그 세 클라이언트가 조용히 다른 URL을 호출하게 되므로, 배선
// 이전(하드코딩 시절)과 동일한 URL을 여전히 만들어내는지 여기서 고정한다.
describe("apiPaths — clients 배선(task-840) 회귀 가드", () => {
  it("executions.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("executions.base")).toBe("/executions");
    expect(resolvePath("executions.start").replace(":executionId", "7")).toBe("/executions/7/start");
    expect(resolvePath("executions.pause").replace(":executionId", "7")).toBe("/executions/7/pause");
    expect(resolvePath("executions.retire").replace(":executionId", "7")).toBe("/executions/7/retire");
    expect(resolvePath("executions.convertToLive").replace(":executionId", "7")).toBe(
      "/executions/7/convert-to-live",
    );
    expect(resolvePath("executions.riskGuard").replace(":executionId", "7")).toBe("/executions/7/risk-guard");
  });

  it("marketplace.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("marketplace.listings.base")).toBe("/marketplace/listings");
    expect(resolvePath("marketplace.listings.submitVerification").replace(":listingId", "3")).toBe(
      "/marketplace/listings/3/submit-verification",
    );
    expect(resolvePath("marketplace.listings.verify").replace(":listingId", "3")).toBe(
      "/marketplace/listings/3/verify",
    );
    expect(resolvePath("marketplace.listings.purchase").replace(":listingId", "3")).toBe(
      "/marketplace/listings/3/purchase",
    );
    expect(
      resolvePath("marketplace.strategies.get").replace(":strategyId", "abc").replace(":version", "2"),
    ).toBe("/marketplace/strategies/abc/2");
    expect(resolvePath("marketplace.listings.reviews").replace(":listingId", "3")).toBe(
      "/marketplace/listings/3/reviews",
    );
    expect(resolvePath("marketplace.disputes.create")).toBe("/marketplace/disputes");
  });

  it("portfolio.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("portfolio.get")).toBe("/portfolio");
    expect(resolvePath("portfolio.rebalance")).toBe("/portfolio/rebalance");
    expect(resolvePath("wallet.balance")).toBe("/wallet/balance");
    expect(resolvePath("wallet.topupRequests")).toBe("/wallet/topup-requests");
    expect(resolvePath("alerts.base")).toBe("/alerts");
    expect(resolvePath("alerts.cancel").replace(":alertId", "9")).toBe("/alerts/9/cancel");
    expect(resolvePath("reports.generate")).toBe("/reports");
  });
});

// task-942: account.ts·admin.ts·auth.ts·exchange.ts·foundation.ts·
// notifications.ts·strategyBuilder.ts도 resolvePath(route)를 경유하도록 바뀌었다
// (platform.ts의 "/readyz"는 아래 INFRA_PATHS 예외로 API_ROUTES 등록 대상에서
// 영구 제외 — decision 확정, needs_decision 아님).
// task-840과 동일한 이유로 이 라우트들의 legacyPath가 하드코딩 시절과 동일한 URL을
// 여전히 만들어내는지 고정한다.
describe("apiPaths — clients 배선(task-942) 회귀 가드", () => {
  it("account.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("account.riskAssessment")).toBe("/users/me/risk-assessment");
    expect(resolvePath("account.riskProfile")).toBe("/users/me/risk-profile");
    expect(resolvePath("account.riskProfileHistory")).toBe("/users/me/risk-profile/history");
    expect(resolvePath("account.approvalSettings")).toBe("/users/me/approval-settings");
    expect(resolvePath("account.whitelist")).toBe("/users/me/withdrawal-whitelist");
    expect(resolvePath("account.deletion")).toBe("/users/me/delete");
    expect(resolvePath("account.approvalRequests.list")).toBe("/users/me/approval-requests");
    expect(resolvePath("account.approvalRequests.approve").replace(":requestId", "5")).toBe(
      "/users/me/approval-requests/5/approve",
    );
    expect(resolvePath("account.approvalRequests.reject").replace(":requestId", "5")).toBe(
      "/users/me/approval-requests/5/reject",
    );
  });

  it("admin.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("admin.verificationQueue")).toBe("/admin/verification-queue");
    expect(resolvePath("admin.disputes.list")).toBe("/admin/disputes");
    expect(resolvePath("admin.disputes.get").replace(":disputeId", "9")).toBe("/admin/disputes/9");
    expect(resolvePath("admin.disputes.resolve").replace(":disputeId", "9")).toBe("/admin/disputes/9/resolve");
    expect(resolvePath("admin.users.list")).toBe("/admin/users");
    expect(resolvePath("admin.users.status").replace(":userId", "u1")).toBe("/admin/users/u1/status");
    expect(resolvePath("admin.users.suspendSeller").replace(":userId", "u1")).toBe(
      "/admin/users/u1/suspend-seller",
    );
    expect(resolvePath("admin.wallet.topupsPending")).toBe("/admin/wallet/topups/pending");
    expect(resolvePath("admin.wallet.topupConfirm").replace(":topupId", "4")).toBe(
      "/admin/wallet/topups/4/confirm",
    );
    expect(resolvePath("admin.marketplace.platformListings")).toBe("/admin/marketplace/platform-listings");
    expect(resolvePath("admin.approvalRequests.approve").replace(":requestId", "6")).toBe(
      "/admin/approval-requests/6/approve",
    );
    expect(resolvePath("admin.approvalRequests.reject").replace(":requestId", "6")).toBe(
      "/admin/approval-requests/6/reject",
    );
    expect(resolvePath("admin.approvalRequests.pending")).toBe("/admin/approval-requests/pending");
  });

  it("auth.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("auth.register")).toBe("/auth/register");
    expect(resolvePath("auth.login")).toBe("/auth/login");
    expect(resolvePath("auth.logout")).toBe("/auth/logout");
    expect(resolvePath("auth.mfaSetup")).toBe("/auth/mfa/setup");
    expect(resolvePath("auth.mfaVerify")).toBe("/auth/mfa/verify");
    expect(resolvePath("auth.me")).toBe("/users/me");
  });

  it("exchange.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("exchange.credentials.base")).toBe("/exchange-credentials");
    expect(resolvePath("exchange.credentials.item").replace(":exchange", "binance")).toBe(
      "/exchange-credentials/binance",
    );
    expect(resolvePath("exchange.credentials.balance").replace(":exchange", "binance")).toBe(
      "/exchange-credentials/binance/balance",
    );
    expect(resolvePath("exchange.credentials.capabilities").replace(":exchange", "binance")).toBe(
      "/exchange-credentials/binance/capabilities",
    );
  });

  it("notifications.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("notifications.history")).toBe("/notifications/history");
    expect(resolvePath("notifications.preferences")).toBe("/notifications/preferences");
    expect(resolvePath("deviceTokens.register")).toBe("/device-tokens");
    expect(resolvePath("deviceTokens.deactivate").replace(":deviceId", "3")).toBe("/device-tokens/3");
  });

  it("strategyBuilder.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("strategyBuilder.indicators.list")).toBe("/strategy-builder/indicators");
    expect(resolvePath("strategyBuilder.strategies.base")).toBe("/strategy-builder/strategies");
    expect(resolvePath("strategyBuilder.candles")).toBe("/strategy-builder/candles");
    expect(resolvePath("strategyBuilder.indicators.compute").replace(":name", "rsi")).toBe(
      "/strategy-builder/indicators/rsi/compute",
    );
    expect(
      resolvePath("strategyBuilder.strategies.get").replace(":strategyId", "abc").replace(":version", "2"),
    ).toBe("/strategy-builder/strategies/abc/2");
    expect(resolvePath("strategyBuilder.preview")).toBe("/strategy-builder/preview");
    expect(resolvePath("strategyBuilder.wizard")).toBe("/strategy-builder/wizard");
    expect(resolvePath("strategyBuilder.generateFromPrompt")).toBe("/strategy-builder/generate-from-prompt");
  });

  it("foundation.ts가 참조하는 라우트의 legacyPath가 하드코딩 시절과 동일하다", () => {
    expect(resolvePath("foundation.paperDeployments.request")).toBe("/v1/foundation/paper-deployments");
    expect(resolvePath("foundation.paperDeployments.start").replace(":deploymentId", "d1")).toBe(
      "/v1/foundation/paper-deployments/d1:start",
    );
    expect(resolvePath("foundation.paperDeployments.resume").replace(":deploymentId", "d1")).toBe(
      "/v1/foundation/paper-deployments/d1:resume",
    );
    expect(resolvePath("foundation.paperDeployments.pause").replace(":deploymentId", "d1")).toBe(
      "/v1/foundation/paper-deployments/d1:pause",
    );
    expect(resolvePath("foundation.paperDeployments.stop").replace(":deploymentId", "d1")).toBe(
      "/v1/foundation/paper-deployments/d1:stop",
    );
    expect(resolvePath("foundation.trustConsents.accept")).toBe("/v1/foundation/trust/consents");
  });
});

// task-942 decision: "/readyz"·"/livez"·"/metrics"는 spec §3.2/§9 PLT-09가 정의하는
// 인프라 프로브다 — 봉투 미적용이고 /api/v1 버저닝 대상도 아니라서(API_ROUTES는
// 버저닝 이관용 70경로 레지스트리) platform.ts는 이 경로들을 resolvePath 없이
// 직접 호출한다. 이는 누락이 아니라 의도이므로, 이 목록이 API_ROUTES에 실수로
// 등록되지 않는지를 여기서 고정해 둔다.
const INFRA_PATHS = ["/readyz", "/livez", "/metrics"];

describe("apiPaths — 인프라 프로브 제외(task-942 decision)", () => {
  it("INFRA_PATHS는 API_ROUTES에 등록되지 않는다(버저닝 대상 아님)", () => {
    const legacyPaths = new Set(Object.values(API_ROUTES).map((def) => def.legacyPath));
    for (const path of INFRA_PATHS) {
      expect(legacyPaths.has(path)).toBe(false);
    }
  });
});

// task-1107: executions.py/portfolio.py/notifications.py/alerts.py/wallet.py/
// reports.py/device_tokens.py 실코드(각 @router 핸들러 반환 타입)를 직접 읽어
// 확인한 결과, 전부 도메인 모델을 그대로 반환할 뿐 ApiResponse 봉투를 쓰지
// 않는다 — executions.py·portfolio.py 모듈 docstring은 "성공 응답 봉투화는
// PLT-17 decision과 동일 사유로 보류"라고 명시하고, wallet.py 모듈 docstring은
// "mount_v1(PLT-16) 배선 이후 별도 리프에서 /api/v1 경로에만 적용"이라고
// 명시한다. executions.ts/portfolio.ts/notifications.ts 클라이언트는 이미
// 이 사실을 정확히 주석에 반영해 두었으므로(코드 변경 없음), apiPaths.ts
// 레지스트리가 실수로 envelope:true로 바뀌는 회귀만 여기서 고정한다
// (marketplace.test.ts의 task-1106 패턴과 동일).
describe("apiPaths — task-1107 봉투 미적용 상태 고정(executions/portfolio/notifications)", () => {
  it.each([
    "executions.base",
    "executions.start",
    "executions.pause",
    "executions.retire",
    "executions.convertToLive",
    "executions.riskGuard",
    "portfolio.get",
    "portfolio.rebalance",
    "wallet.balance",
    "wallet.topupRequests",
    "alerts.base",
    "alerts.cancel",
    "reports.generate",
    "notifications.history",
    "notifications.preferences",
    "deviceTokens.register",
    "deviceTokens.deactivate",
  ] as const)("%s: envelope=false", (routeName) => {
    expect(API_ROUTES[routeName].envelope).toBe(false);
  });
});
