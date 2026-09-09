import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { API_ROUTES, type ApiRouteDefinition, type ApiRouteName } from "./apiPaths";
import {
  computeEnvelopeFromOpenApi,
  findUnregisteredSnapshotPaths,
  resolveInOpenApi,
} from "./apiPaths.openapi.scanner";

// task-1165: apiPaths.ts(사람이 손으로 등록한 표)와 PLT-16(task-905, f800c1a)이 export한
// contracts/openapi/v1.json(서버 라우터의 기계적 단일출처, 101경로) 사이에 대조가 한 번도
// 없었다 — 라우터가 바뀌어도 표가 조용히 낡을 수 있었다(task-1160 decision이 손으로
// account.riskProfile 드리프트 부재를 확인해야 했던 이유). 이 파일은 그 대조를 고정한다.
//
// v1Path는 여기서 단언하지 않는다: mount_v1(src/api/versioning.py)이 아직 main.py에
// 배선되지 않아(PLT-16이 PLT-17~21로 의도적으로 미룸) 스냅샷에 "/api/v1" 접두 경로가
// 0건이다 — 스냅샷의 경로는 사실상 legacyPath와 같은 네임스페이스다. 없는 v1 배선을
// 있다고 단언하면 거짓 양성이 된다.
const SNAPSHOT_PATH = join(dirname(fileURLToPath(import.meta.url)), "../../../../contracts/openapi/v1.json");

// 파일이 없으면 조용히 skip하지 않는다(I-10) — readFileSync가 없으면 그대로 throw해서
// 이 테스트 파일 전체가 FAIL로 보고된다.
const snapshotRaw = readFileSync(SNAPSHOT_PATH, "utf-8");
const snapshot: { paths: Record<string, Record<string, unknown>> } = JSON.parse(snapshotRaw);
const snapshotPathList = Object.keys(snapshot.paths);
const snapshotPathSet = new Set(snapshotPathList);

// task-719/824: apiPaths.ts 자체 코멘트가 이미 "라우터 자체가 아직 없음"이라고 명시한
// 4개 경로 — src/api/routers/에는 market_data(.py) 라우터가 존재하지 않고(디렉터리
// 목록 직접 확인), 그래서 PLT-16 스냅샷(101경로)에도 대응 항목이 없다. "전부 예외"로
// 도망가지 않기 위해 개별로 남긴다: route명 / 등록값(v1Path=null) / 스냅샷 JSON
// 포인터(없음 — paths에 키 자체가 없음) / 실제 라우터 파일(없음, src/api/routers 목록에
// admin/alerts/auth/device_tokens/exchange_credentials/executions/foundation/health/
// marketplace/metrics/notifications/portfolio/reports/strategy_builder/suitability/
// users/wallet.py뿐).
// task-1325: sessions.ts(task-606)가 실제로 호출하지만(GET /auth/sessions·DELETE
// /auth/sessions/:id) src/api/routers에 세션 라우터 자체가 없다(auth.py는
// register/login/refresh/logout/logout-all/mfa/setup/mfa/verify뿐) — marketData.*와
// 같은 이유의 유령 경로다. apiPaths.ts의 auth.sessions.*는 implemented=false로도
// 등록돼 있고(§D가 그 일치를 강제), sessions.ts는 그 플래그로 네트워크 호출 전에
// typed 오류(SessionsRouteNotImplementedError)를 던진다.
// task-1376(LA-24): marketData.* 4건은 src/api/routers/market_data.py가 실재하게
// 되어(GET /v1/foundation/market-data/candles·candles/replay·instruments·
// instruments/{symbol}/aliases, main.py include_router) 더 이상 유령 경로가
// 아니다 — 아래 STALE_SNAPSHOT_WHITELIST로 옮겼다(스냅샷 재생성 전).
const GHOST_PATH_WHITELIST: ReadonlySet<ApiRouteName> = new Set<ApiRouteName>([
  "auth.sessions.list",
  "auth.sessions.revoke",
]);

// STALE_SNAPSHOT_WHITELIST: GHOST_PATH_WHITELIST와는 다른 사유 — "라우터가
// 없다"가 아니라 "라우터는 있는데 스냅샷이 낡았다"다.
// task-1344(QA, task-1324 검증): 60e0b8d7(chore(ci) OpenAPI 기준선 재생성)로
// contracts/openapi/v1.json이 갱신되면서 이 목록이 부패했다 — auth.refresh·
// auth.logoutAll(task-1324)·marketData.*(task-1376/1525)·positions.*
// (task-1524)·scripts.compile(task-1558)·charting.layouts.*(task-1593)·
// backtests.quick(task-1607) 14건은 이제 전부 스냅샷에 실재하고(python으로
// contracts/openapi/v1.json paths 직접 대조 완료), envelope도 apiPaths.ts의
// 등록값(전부 true)과 일치한다(§B 래칫 재계산 결과 drift 0) — "화이트리스트
// 부패 방지" 테스트가 정확히 이 14건을 nowPresent로 잡아냈으므로 제거한다.
// task-1905(CH-17c): charting.indicatorTemplates.* 2건은 60e0b8d7 재생성
// 시점에도 여전히 스냅샷에 없다(router가 그 이후 머지) — 그대로 남긴다.
// task-2196(DC-18b): marketData.coverage.get은 task-2195(DC-18a, ddacfca6)로
// src/api/routers/market_data.py에 실재하게 됐지만(GET .../market-data/coverage),
// 스냅샷은 그 병합 이후 재생성된 적이 없다 — charting.indicatorTemplates.*와
// 동일 사유(라우터는 있는데 스냅샷이 낡음)라 STALE_SNAPSHOT_WHITELIST에 둔다.
// task-2335(FE-OPS-1): riskGate.safetyControls.evaluateRecovery도 동일 사유 —
// src/api/routers/foundation/risk_gate.py:161 post_evaluate_recovery는 실재하지만
// contracts/openapi/v1.json에는 없다(grep 직접 확인, risk-gate 6경로 중 유일).
const STALE_SNAPSHOT_WHITELIST: ReadonlySet<ApiRouteName> = new Set<ApiRouteName>([
  "charting.indicatorTemplates.base",
  "charting.indicatorTemplates.item",
  "marketData.coverage.get",
  "riskGate.safetyControls.evaluateRecovery",
]);

// KNOWN_ENVELOPE_DRIFT: task-1165 시점 전수 대조 결과, apiPaths.ts에 등록된 envelope
// 값과 스냅샷이 실제로 말하는 봉투 여부 사이에 불일치가 0건이었다(GHOST_PATH_WHITELIST
// 4건 제외 70건 전부 일치, account.riskProfile 포함 — task-1160 decision이 "드리프트
// 아님"이라고 손으로 확인한 것과 동일 결론을 여기서 기계적으로 재확인한다). 새 드리프트가
// 생기면 여기 추가하지 말고 route명/등록값/스냅샷 JSON 포인터/실제 라우터 파일:라인을
// 근거로 needs_decision으로 올려라 — 이 리프는 봉투 값을 고치는 리프가 아니다.
//
// task-1309가 한때 여기 추가했던 foundation.paperDeployments.*(5건)·
// foundation.trustConsents.accept(1건)는 task-1344(QA, task-1324 검증) 시점에
// 제거됐다 — 60e0b8d7(OpenAPI 기준선 재생성)로 스냅샷이 갱신되면서 두 라우터의
// PaperDeploymentView/ConsentDecision 직접 노출이 사라지고 실제로도
// ApiResponse[...]를 참조한다(python으로 contracts/openapi/v1.json 재확인 —
// paths["/v1/foundation/paper-deployments"] 등 7개 경로 전부 True). apiPaths.ts의
// true와 다시 일치하므로 드리프트가 아니다.
const KNOWN_ENVELOPE_DRIFT: ReadonlySet<ApiRouteName> = new Set<ApiRouteName>([]);

// UNREGISTERED_ROUTE_WHITELIST(task-2168 §E, 역방향): v1.json에는 있지만 apiPaths.ts
// API_ROUTES에는 등록되지 않은 서버 라우트. §A가 잡는 유령 경로(등록됐지만 스냅샷에
// 없음)의 반대 방향 — 여기서는 "화면이 실제로 쓰는데 레지스트리를 우회한 하드코딩
// 호출"이 없는지를 본다. 착수 시점(task-2168, 2026-09-08) 실측 38건 — apps/web/src/routes
// 전체와 packages/api-client/src/clients/*.ts를 grep으로 대조(connections/evidence/
// mandates/performance-statements/reconciliation/risk-gate/trust-memberships/
// validation-runs/audit-log/ledger-payouts 키워드 매치 0건, PositionJournalPanel 등의
// "positions" 매치는 이미 등록된 positions.* 클라이언트였다) — 화면이 호출해야 하는
// 항목이 하나도 없어 이번 리프에서 apiPaths에 새로 등록할 라우트는 없다. 값은 "왜
// 프론트가 안 쓰는지" 한 줄.
// task-2412(FE-OPS-8): validation-runs/{strategy_id}/{strategy_version}은 이제 화면이
// 실제로 부른다(StrategyBuilderPage → ValidationRunPanel → validation.start,
// apiRoutes.ts에 등록) — 여기 있던 항목을 제거한다.
const UNREGISTERED_ROUTE_WHITELIST: Readonly<Record<string, string>> = {
  "/admin/audit-log": "관리자 감사 로그 화면이 없다(apps/web/src/routes/admin에 audit-log 라우트 없음)",
  "/admin/ledger/payouts/{batch_id}/paid": "정산 배치 확정 액션 UI가 없다",
  "/exchange-credentials/{exchange}/positions": "exchange.ts 클라이언트에 balance/capabilities만 있고 positions 조회는 없다",
  "/livez": "인프라 헬스체크 프로브다(k8s liveness) — 앱 API 표면이 아니다",
  "/metrics": "인프라 메트릭 엔드포인트다(모니터링 전용) — 앱 API 표면이 아니다",
  "/readyz": "인프라 헬스체크 프로브다(k8s readiness) — 앱 API 표면이 아니다",
  "/v1/foundation/connections": "계정 연동(connections) 관리 화면이 없다(apps/web/src/routes에 connections 없음)",
  "/v1/foundation/connections/{connection_id}:confirm": "계정 연동 확인 액션 UI가 없다",
  "/v1/foundation/connections/{connection_id}:revoke": "계정 연동 해제 액션 UI가 없다",
  "/v1/foundation/connections/{connection_id}:sync": "계정 연동 동기화 액션 UI가 없다",
  // task-2337(FE-OPS-3): EvidenceChainPage가 timeline/chain:verify 2건을 evidence.*로
  // 등록했다 — 여기 있던 2개 항목(task-2168 원 목록)을 제거한다.
  // task-2336(FE-OPS-2): MandatesPage가 status/drafts/amendments/revisions/
  // {revision_id}:activate/mandate:pause/mandate:resume/policy:evaluate 7건 전부를
  // mandates.*로 등록했다 — 여기 남아 있던 7개 항목(task-2168 원 목록)을 제거한다.
  "/v1/foundation/performance-statements": "실적 명세서 화면이 없다",
  "/v1/foundation/performance-statements/{statement_id}": "실적 명세서 화면이 없다",
  "/v1/foundation/performance-statements/{statement_id}:correct": "실적 명세서 정정 액션 UI가 없다",
  "/v1/foundation/performance-statements:compute": "실적 명세서 계산 액션 UI가 없다",
  // task-2337(FE-OPS-3): ReconciliationPage가 목록 조회·해소(resolve) 2건을
  // reconciliation.*로 등록했다 — 실행 이력(POST /runs)은 decision상 이 리프의
  // UI 범위 밖이라 그대로 남긴다(사람이 EntitySnapshot을 입력해 만드는 화면이 없다).
  "/v1/foundation/reconciliation/runs": "정합성 대사 실행 이력 화면이 없다",
  // task-2335(FE-OPS-1): SafetyControlsPage가 GET(list)/deactivate/evaluate-recovery
  // 3건을 riskGate.safetyControls.*로 등록했다 — 아래 3건은 decision상 이 리프가
  // 만들지 않는 개통(activate)·룰번들 승인/활성화·evaluate 트리거라 그대로 남긴다
  // (후속 리프 2336~2338 소관).
  "/v1/foundation/risk-gate/admin/safety-controls": "리스크 게이트 관리자 개통(activate) UI가 없다(decision: 이 리프는 읽기·해제만)",
  "/v1/foundation/risk-gate/evaluate": "리스크 게이트 평가 트리거 화면이 없다",
  "/v1/foundation/risk-gate/rule-bundles/{bundle_id}:activate": "룰번들 활성화 액션 UI가 없다",
  "/v1/foundation/risk-gate/rule-bundles/{bundle_id}:approve": "룰번들 승인 액션 UI가 없다",
  "/v1/foundation/trust/consents/{consent_id}:revoke": "동의 철회 액션 UI가 없다(accept만 foundation.trustConsents.accept로 등록돼 있음)",
  "/v1/foundation/trust/memberships": "신뢰 멤버십 관리 화면이 없다",
  "/v1/foundation/trust/memberships/{subject_id}:revoke": "신뢰 멤버십 관리 화면이 없다",
  "/v1/foundation/trust/memberships/{subject_id}:suspend": "신뢰 멤버십 관리 화면이 없다",
  "/v1/foundation/trust/status": "신뢰 상태 조회 화면이 없다",
};

function nonGhostRouteEntries(): Array<[ApiRouteName, ApiRouteDefinition]> {
  return (Object.entries(API_ROUTES) as Array<[ApiRouteName, ApiRouteDefinition]>).filter(
    ([name]) => !GHOST_PATH_WHITELIST.has(name) && !STALE_SNAPSHOT_WHITELIST.has(name),
  );
}

describe("apiPaths ↔ contracts/openapi/v1.json — 경로 정합성(task-1165 §A)", () => {
  it("GHOST_PATH_WHITELIST 밖의 모든 legacyPath는 스냅샷에 실재한다(유령 경로 0건)", () => {
    const missing: string[] = [];
    for (const [name, def] of nonGhostRouteEntries()) {
      if (resolveInOpenApi(def.legacyPath, snapshotPathList) === null) {
        missing.push(`${name} (${def.legacyPath})`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("GHOST_PATH_WHITELIST 항목은 실제로 스냅샷에 없다(화이트리스트 부패 방지)", () => {
    const nowPresent: string[] = [];
    for (const name of GHOST_PATH_WHITELIST) {
      const def = API_ROUTES[name];
      if (resolveInOpenApi(def.legacyPath, snapshotPathList) !== null) {
        nowPresent.push(`${name} (${def.legacyPath})`);
      }
    }
    expect(nowPresent).toEqual([]);
  });

  it("STALE_SNAPSHOT_WHITELIST 항목은 실제로 스냅샷에 없다(화이트리스트 부패 방지)", () => {
    const nowPresent: string[] = [];
    for (const name of STALE_SNAPSHOT_WHITELIST) {
      const def = API_ROUTES[name];
      if (resolveInOpenApi(def.legacyPath, snapshotPathList) !== null) {
        nowPresent.push(`${name} (${def.legacyPath})`);
      }
    }
    expect(nowPresent).toEqual([]);
  });
});

describe("apiPaths ↔ contracts/openapi/v1.json — 봉투 드리프트 래칫(task-1165 §B)", () => {
  it("등록된 envelope 값과 스냅샷의 실제 봉투 여부 불일치 집합이 KNOWN_ENVELOPE_DRIFT와 정확히 일치한다", () => {
    const drifted = new Set<ApiRouteName>();
    for (const [name, def] of nonGhostRouteEntries()) {
      const snapshotPath = resolveInOpenApi(def.legacyPath, snapshotPathList);
      if (snapshotPath === null) continue; // §A가 이미 커버(유령 경로)
      const snapshotEnvelope = computeEnvelopeFromOpenApi(snapshot.paths[snapshotPath]);
      if (snapshotEnvelope === null) continue; // 스냅샷에 판단 근거(2xx json 스키마)가 없는 경로
      if (snapshotEnvelope !== def.envelope) drifted.add(name);
    }
    // 새 드리프트도 FAIL, 이미 고쳐진(더 이상 실재하지 않는) 드리프트도 FAIL(양방향).
    expect([...drifted].sort()).toEqual([...KNOWN_ENVELOPE_DRIFT].sort());
  });
});

describe("apiPaths ↔ contracts/openapi/v1.json — 스캐너 자체 검증(task-1165 §C, negative)", () => {
  it("resolveInOpenApi는 없는 경로를 fixture로 주면 실제로 null을 돌려준다", () => {
    const fixturePaths = ["/known/path", "/known/{id}/detail"];
    expect(resolveInOpenApi("/known/path", fixturePaths)).toBe("/known/path");
    expect(resolveInOpenApi("/known/:id/detail", fixturePaths)).toBe("/known/{id}/detail");
    expect(resolveInOpenApi("/totally/unregistered/path", fixturePaths)).toBeNull();
  });

  it("resolveInOpenApi는 ':param:literal' 복합 세그먼트를 fixture로 검증한다", () => {
    const fixturePaths = ["/deployments/{deployment_id}:start"];
    expect(resolveInOpenApi("/deployments/:deploymentId:start", fixturePaths)).toBe(
      "/deployments/{deployment_id}:start",
    );
    expect(resolveInOpenApi("/deployments/:deploymentId:stop", fixturePaths)).toBeNull();
  });

  it("computeEnvelopeFromOpenApi는 ApiResponse_* 참조가 있으면 true, 도메인 모델 직접 반환이면 false를 fixture로 검증한다", () => {
    const enveloped = { get: { responses: { "200": { content: { "application/json": { schema: { $ref: "#/components/schemas/ApiResponse_Foo_" } } } } } } };
    const bare = { get: { responses: { "200": { content: { "application/json": { schema: { $ref: "#/components/schemas/Foo" } } } } } } };
    const empty = { delete: { responses: { "204": {} } } };
    expect(computeEnvelopeFromOpenApi(enveloped)).toBe(true);
    expect(computeEnvelopeFromOpenApi(bare)).toBe(false);
    expect(computeEnvelopeFromOpenApi(empty)).toBeNull();
  });

  it("틀린 봉투를 fixture로 주입하면 §B와 동일한 대조 로직이 실제로 드리프트를 잡아낸다", () => {
    const fakeRoutes: Array<[string, ApiRouteDefinition]> = [
      { legacyPath: "/fake/enveloped", envelope: false } as ApiRouteDefinition,
    ].map((def) => ["fake.route", def]);
    const fakeSnapshotPaths = ["/fake/enveloped"];
    const fakeSnapshot = {
      "/fake/enveloped": {
        get: {
          responses: {
            "200": { content: { "application/json": { schema: { $ref: "#/components/schemas/ApiResponse_Fake_" } } } },
          },
        },
      },
    };
    const drifted: string[] = [];
    for (const [name, def] of fakeRoutes) {
      const snapshotPath = resolveInOpenApi(def.legacyPath, fakeSnapshotPaths);
      expect(snapshotPath).not.toBeNull();
      const snapshotEnvelope = computeEnvelopeFromOpenApi(fakeSnapshot[snapshotPath as string]);
      if (snapshotEnvelope !== def.envelope) drifted.push(name);
    }
    expect(drifted).toEqual(["fake.route"]);
  });
});

describe("apiPaths ↔ GHOST_PATH_WHITELIST — implemented 플래그 정합(task-1325 §D)", () => {
  it("GHOST_PATH_WHITELIST 항목은 전부 implemented=false로 등록돼 있다", () => {
    const mismatched: string[] = [];
    for (const name of GHOST_PATH_WHITELIST) {
      if (API_ROUTES[name].implemented) mismatched.push(name);
    }
    expect(mismatched).toEqual([]);
  });
  it("GHOST_PATH_WHITELIST 밖의 모든 라우트는 implemented=true다(유령 경로가 몰래 추가되면 실패)", () => {
    const mismatched: string[] = [];
    for (const [name, def] of Object.entries(API_ROUTES) as Array<[ApiRouteName, ApiRouteDefinition]>) {
      if (!GHOST_PATH_WHITELIST.has(name) && !def.implemented) mismatched.push(name);
    }
    expect(mismatched).toEqual([]);
  });
});

describe("contracts/openapi/v1.json ↔ apiPaths — 역방향 정합성(task-2168 §E)", () => {
  it("스냅샷에만 있고 apiPaths에 없는 경로 집합이 UNREGISTERED_ROUTE_WHITELIST와 정확히 일치한다", () => {
    const legacyPaths = Object.values(API_ROUTES).map((def) => (def as ApiRouteDefinition).legacyPath);
    const unregistered = findUnregisteredSnapshotPaths(snapshotPathList, legacyPaths);
    expect([...unregistered].sort()).toEqual(Object.keys(UNREGISTERED_ROUTE_WHITELIST).sort());
  });

  it("UNREGISTERED_ROUTE_WHITELIST 항목은 실제로 스냅샷에 있고 apiPaths에는 등록돼 있지 않다(화이트리스트 부패 방지)", () => {
    const legacyPaths = Object.values(API_ROUTES).map((def) => (def as ApiRouteDefinition).legacyPath);
    const bad: string[] = [];
    for (const path of Object.keys(UNREGISTERED_ROUTE_WHITELIST)) {
      const inSnapshot = snapshotPathSet.has(path);
      const stillUnregistered = findUnregisteredSnapshotPaths([path], legacyPaths).length === 1;
      if (!inSnapshot || !stillUnregistered) bad.push(path);
    }
    expect(bad).toEqual([]);
  });

  it("이미 등록된 라우트(marketData.candles.get)를 apiPaths에서 지운 fixture를 주면 그 경로명을 지목한다(반증)", () => {
    const candlesPath = API_ROUTES["marketData.candles.get"].legacyPath;
    const legacyPathsWithoutCandles = Object.entries(API_ROUTES)
      .filter(([name]) => name !== "marketData.candles.get")
      .map(([, def]) => (def as ApiRouteDefinition).legacyPath);

    const unregisteredWithout = findUnregisteredSnapshotPaths(snapshotPathList, legacyPathsWithoutCandles);
    expect(unregisteredWithout).toContain(candlesPath);

    const legacyPathsWithCandles = Object.values(API_ROUTES).map((def) => (def as ApiRouteDefinition).legacyPath);
    const unregisteredWith = findUnregisteredSnapshotPaths(snapshotPathList, legacyPathsWithCandles);
    expect(unregisteredWith).not.toContain(candlesPath);
  });
});
