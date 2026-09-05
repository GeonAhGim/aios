import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { API_ROUTES, type ApiRouteDefinition, type ApiRouteName } from "./apiPaths";

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
// 없다"가 아니라 "라우터는 있는데 스냅샷이 낡았다"다. task-1324: origin/main
// 0a68f86의 src/api/routers/auth.py 원문을 직접 읽어 확인한 결과 POST
// /auth/refresh·POST /auth/logout-all이 실재하고(PLT-24, task-1075, e0eb498
// 병합) 각각 ApiResponse[TokenPairResponse]·ApiResponse[dict[str,int]]를
// 반환한다. 그런데 contracts/openapi/v1.json은 task-905(f800c1a) 시점 이후
// 재생성된 적이 없어(PLT-17~21 foundation.* envelope 이관 등 이 leaf와 무관한
// 드리프트가 250건+ 함께 쌓여 있다 — 로컬 재생성은 그 무관한 변경까지 한
// 커밋에 섞어 "백엔드 diff 0" 원칙을 어기게 되므로 이 leaf 범위 밖이다) 이
// 두 경로가 아직 없다. 스냅샷이 갱신되면(별도 PLT 리프) 이 화이트리스트가
// 거짓이 되므로 아래 "화이트리스트 부패 방지" 테스트가 잡는다.
//
// task-1376(LA-24): marketData.* 4건 — 라우터는 src/api/routers/market_data.py에
// 실재하지만 스냅샷은 여전히 f800c1a 시점이라 경로가 없다(auth.refresh와 같은
// 사유). 스냅샷이 재생성되면 아래 "부패 방지" 테스트가 잡는다.
const STALE_SNAPSHOT_WHITELIST: ReadonlySet<ApiRouteName> = new Set<ApiRouteName>([
  "auth.refresh",
  "auth.logoutAll",
  "marketData.candles.get",
  "marketData.candles.replay",
  "marketData.instruments.list",
  "marketData.instruments.aliases",
]);

// KNOWN_ENVELOPE_DRIFT: task-1165 시점 전수 대조 결과, apiPaths.ts에 등록된 envelope
// 값과 스냅샷이 실제로 말하는 봉투 여부 사이에 불일치가 0건이었다(GHOST_PATH_WHITELIST
// 4건 제외 70건 전부 일치, account.riskProfile 포함 — task-1160 decision이 "드리프트
// 아님"이라고 손으로 확인한 것과 동일 결론을 여기서 기계적으로 재확인한다). 새 드리프트가
// 생기면 여기 추가하지 말고 route명/등록값/스냅샷 JSON 포인터/실제 라우터 파일:라인을
// 근거로 needs_decision으로 올려라 — 이 리프는 봉투 값을 고치는 리프가 아니다.
//
// task-1309가 추가한 6건은 그 원칙의 예외가 아니라 시간순으로 검증된 사실이다.
// 커밋 시각: f800c1a(task-905, 이 스냅샷 생성) 2026-09-03 18:45 → e4c9bd6
// (task-1217 PLT-21b-2, "foundation 라우터 봉투·예외 이관") 2026-09-04 10:21 —
// paper_control.py/trust.py가 스냅샷 생성 "이후" ApiResponse[T]로 이관됐다.
// 스냅샷 포인터: paths["/v1/foundation/paper-deployments"].post.responses.201
// .content["application/json"].schema.$ref === "#/components/schemas/
// PaperDeploymentView"(ApiResponse_* 아님 — 이관 전 스냅샷). 실제 라우터 원본
// (src/api/routers/foundation/paper_control.py, 현재 HEAD 기준)은 6개 엔드포인트
// 전부 `-> ApiResponse[...]` + `return ok(...)`이고, trust.py의 post_accept_disclosure도
// 동일(`-> ApiResponse[ConsentDecision]`) — apiPaths.ts의 true가 "지금" 맞는 값이고
// 스냅샷(f800c1a)이 낡았다. 스냅샷 재생성은 이 leaf 범위 밖(무관한 변경까지 섞여
// "백엔드 diff 0" 원칙 위반, STALE_SNAPSHOT_WHITELIST 주석과 동일 사유)이라
// 별도 PLT 리프가 export_openapi를 재실행할 때까지 이 6건은 여기 남는다.
const KNOWN_ENVELOPE_DRIFT: ReadonlySet<ApiRouteName> = new Set<ApiRouteName>([
  "foundation.paperDeployments.request",
  "foundation.paperDeployments.start",
  "foundation.paperDeployments.resume",
  "foundation.paperDeployments.pause",
  "foundation.paperDeployments.stop",
  "foundation.trustConsents.accept",
]);

// legacyPath의 ":param" / ":param:literal"(예: ":deploymentId:start") 세그먼트를
// 스냅샷의 "{param}" 세그먼트와 비교 가능한 와일드카드 템플릿으로 바꾼다.
function legacyPathToTemplate(legacyPath: string): string {
  return legacyPath
    .split("/")
    .map((segment) => {
      if (!segment.startsWith(":")) return segment;
      const parts = segment.slice(1).split(":");
      return parts.length === 1 ? "*" : `*:${parts.slice(1).join(":")}`;
    })
    .join("/");
}

function snapshotPathToTemplate(snapshotPath: string): string {
  return snapshotPath.replace(/\{[^}]+\}/g, "*");
}

// 정확일치 우선, 실패 시 템플릿 매칭. 어느 쪽도 안 맞으면 null(호출부가 화이트리스트
// 여부를 판단한다 — 이 함수는 화이트리스트를 모른다).
export function resolveInOpenApi(legacyPath: string, paths: readonly string[]): string | null {
  if (paths.includes(legacyPath)) return legacyPath;
  const template = legacyPathToTemplate(legacyPath);
  return paths.find((p) => snapshotPathToTemplate(p) === template) ?? null;
}

// 매칭된 스냅샷 경로의 methods 객체에서 2xx 응답 스키마가 ApiResponse_*를 참조하는지
// 본다(§3.3 봉투 판정: FastAPI가 ApiResponse[T]를 감싼 라우터는 responses.200.content.
// application/json.schema.$ref가 "#/components/schemas/ApiResponse_..."다). 메서드가
// 여럿이면(GET+POST 등) 전부 같은 값이어야 하고, 판단할 데이터가 전혀 없으면 null.
export function computeEnvelopeFromOpenApi(pathItem: Record<string, unknown>): boolean | null {
  const values = new Set<boolean>();
  for (const method of ["get", "post", "put", "patch", "delete"]) {
    const op = pathItem[method] as { responses?: Record<string, unknown> } | undefined;
    if (!op?.responses) continue;
    for (const [code, resp] of Object.entries(op.responses)) {
      if (!code.startsWith("2")) continue;
      const schema = (resp as { content?: { ["application/json"]?: { schema?: Record<string, unknown> } } })
        ?.content?.["application/json"]?.schema;
      if (!schema) continue;
      const ref =
        (schema.$ref as string | undefined) ??
        ((schema.items as Record<string, unknown> | undefined)?.$ref as string | undefined);
      values.add(Boolean(ref?.split("/").pop()?.startsWith("ApiResponse")));
    }
  }
  if (values.size !== 1) return values.size === 0 ? null : null;
  return [...values][0];
}

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
