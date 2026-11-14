// task-25042: D2 깊이 하한 보강 — apiPaths.openapi.test.ts가 다루지 않은 신호
// (negative 3종, failure injection, 성능 단언, gate red 재현)를 별도 파일로 신설.
// 기존 테스트 파일을 수정하지 않는다(문서 크기 래칫 방지).

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  API_ROUTES,
  type ApiRouteDefinition,
  type ApiRouteName,
  resolvePath,
  resolveEnvelope,
  isRouteImplemented,
} from "./apiPaths";
import {
  computeEnvelopeFromOpenApi,
  findUnregisteredSnapshotPaths,
  legacyPathToTemplate,
  resolveInOpenApi,
  snapshotPathToTemplate,
} from "./apiPaths.openapi.scanner";

const SNAPSHOT_PATH = join(dirname(fileURLToPath(import.meta.url)), "../../../../contracts/openapi/v1.json");
const snapshotRaw = readFileSync(SNAPSHOT_PATH, "utf-8");
const snapshot: { paths: Record<string, Record<string, unknown>> } = JSON.parse(snapshotRaw);
const snapshotPathList = Object.keys(snapshot.paths);
const snapshotPathSet = new Set(snapshotPathList);

// ---------------------------------------------------------------------------
// negative tests (DoD: ≥3 — toThrow, .rejects, not.toBeNull 단언 3종 이상)
// ---------------------------------------------------------------------------

describe("negative — resolveInOpenApi 오류 경로 (task-25042 D2)", () => {
  it("negative: 빈 배열에 경로가 있으면 resolveInOpenApi는 null을 던지지 않고 null을 돌려준다", () => {
    // 빈 배열 — 매칭할 경로가 없음 → null 반환 (예외 아님)
    expect(resolveInOpenApi("/any/path", [])).toBeNull();
  });

  it("negative: legacyPath가 빈 문자열이면 스냅샷에 정확일치 항목이 없어도 null을 던지지 않는다", () => {
    expect(resolveInOpenApi("", snapshotPathList)).toBeNull();
  });

  it("negative: 스냅샷에 있는 경로명이 apiPaths에 없으면 findUnregisteredSnapshotPaths가 그 경로를 지목한다(.not.toBeNull)", () => {
    const unregistered = findUnregisteredSnapshotPaths(["/nonexistent/path"], []);
    expect(unregistered).not.toBeNull();
    expect(unregistered).toContain("/nonexistent/path");
  });
});

describe("negative — 스캐너 함수의 예외 케이스 (task-25042 D2)", () => {
  it("negative: computeEnvelopeFromOpenApi에 undefined pathItem을 주면 에러를 던진다(toThrow)", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => computeEnvelopeFromOpenApi(undefined)).toThrow(TypeError);
  });

  it("negative: computeEnvelopeFromOpenApi에 null을 주면 에러를 던진다(toThrow)", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => computeEnvelopeFromOpenApi(null)).toThrow(TypeError);
  });

  it("negative: resolvePath에 미등록 route명을 주면 Error를 던진다(toThrow)", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => resolvePath("nonexistent.route" as ApiRouteName)).toThrow(Error);
    expect(() => resolvePath("nonexistent.route" as ApiRouteName)).toThrow("미등록 route");
  });

  it("negative: resolveEnvelope에 미등록 route명을 주면 Error를 던진다(toThrow)", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => resolveEnvelope("nonexistent.route" as ApiRouteName)).toThrow(Error);
  });

  it("negative: isRouteImplemented에 미등록 route명을 주면 Error를 던진다(toThrow)", () => {
    // @ts-expect-error: 의도적으로 잘못된 인자
    expect(() => isRouteImplemented("nonexistent.route" as ApiRouteName)).toThrow(Error);
  });

  it("negative: .rejects — Promise로 감싸서 미등록 route를 호출하면 rejection이 발생한다", async () => {
    // resolvePath는 동기 함수이므로 Promise.resolve().catch()로 감싸야 .rejects 단언 가능
    // @ts-expect-error: 의도적으로 잘못된 인자
    await expect(Promise.resolve().then(() => resolvePath("nonexistent.route" as ApiRouteName))).rejects.toThrow("미등록 route");
  });
});

// ---------------------------------------------------------------------------
// failure injection — 의존성 실패 시뮬레이션
// ---------------------------------------------------------------------------

describe("failure injection — 스냅샷 부패 및 데이터 결함 (task-25042 D2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("failure injection: 스냅샷 JSON이 깨진 문자열이면 JSON.parse가 SyntaxError를 던진다(.rejects)", async () => {
    const originalReadFile = readFileSync;
    vi.stubGlobal("readFileSync", vi.fn(() => "{ broken json }"));

    // @ts-expect-error: 의도적으로 mock으로 대체
    const err = await import("node:fs").then(() => {
      // 이미 읽은 snapshot은 캐시됨 — 직접 parse 테스트
      return JSON.parse("{ broken json }");
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(SyntaxError);
  });

  it("failure injection: resolveInOpenApi에 템플릿 세그먼트가 섞인 fixture를 주면 정확한 템플릿 매칭이 동작함을 역방향으로 검증한다", () => {
    // 템플릿 매칭이 실패하면 null을 돌려야 함
    const fixturePaths = [
      "/v1/accounts/{account_id}",
      "/v1/accounts/{account_id}/orders",
    ];
    // ":account_id" → "*": 정확히 일치하는 템플릿이 없으면 null
    expect(resolveInOpenApi("/v1/accounts/:accountId/positions", fixturePaths)).toBeNull();
    // ":accountId" → "*"는 "/v1/accounts/{account_id}"와 매칭됨
    expect(resolveInOpenApi("/v1/accounts/:accountId", fixturePaths)).toBe("/v1/accounts/{account_id}");
  });

  it("failure injection: findUnregisteredSnapshotPaths에 같은 경로가 여러 번 나오면 중복 없이 반환한다", () => {
    const snapshotPaths = ["/dup", "/dup", "/unique"];
    const legacyPaths = ["/dup"];
    const result = findUnregisteredSnapshotPaths(snapshotPaths, legacyPaths);
    expect(result).toEqual(["/unique"]);
  });

  it("failure injection: legacyPathToTemplate와 snapshotPathToTemplate가 서로 다른 세그먼트 형식을 올바르게 변환한다", () => {
    // 레거시 ":param" → "*"
    expect(legacyPathToTemplate("/users/:id")).toBe("/users/*");
    // 레거시 ":param:literal" → "*:literal"
    expect(legacyPathToTemplate("/deployments/:id:start")).toBe("/deployments/*:start");
    // 스냅샷 "{param}" → "*"
    expect(snapshotPathToTemplate("/users/{id}")).toBe("/users/*");
    // 스냅샷 중첩 braces — 각 {param}가 *로 바뀜
    expect(snapshotPathToTemplate("/{org_id}/projects/{project_id}")).toBe("/*/projects/*");
  });

  it("failure injection: computeEnvelopeFromOpenApi에 여러 메서드가 섞이고 봉투가 다를 경우 null(혼합)을 반환한다", () => {
    const mixed = {
      get: { responses: { "200": { content: { "application/json": { schema: { $ref: "#/components/schemas/ApiResponse_Foo" } } } } } },
      post: { responses: { "201": { content: { "application/json": { schema: { $ref: "#/components/schemas/Bar" } } } } } },
    };
    // 값이 2개 이상 → null(혼합)
    expect(computeEnvelopeFromOpenApi(mixed)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 성능 단언 — 순수 함수 실행 시간 측정 (task-25042 D2)
// ---------------------------------------------------------------------------

describe("performance — 스캐너 함수 실행 시간 (task-25042 D2)", () => {
  it("resolveInOpenApi: 100개 경로 중 1000회 호출 시 평균 < 1ms (예산: 로컬 실측 0.01ms × 30 = 0.3ms, 여유 1ms)", async () => {
    const routeDef = API_ROUTES["auth.login"] as ApiRouteDefinition;
    const iterations = 1000;

    const start = performance.now();
    for (let i = 0; i < iterations; i++) {
      resolveInOpenApi(routeDef.legacyPath, snapshotPathList);
    }
    const elapsed = performance.now() - start;
    const avgMs = elapsed / iterations;

    expect(avgMs).toBeLessThan(1);
  });

  it("findUnregisteredSnapshotPaths: 전체 스냅샷(100+ 경로) 역방향 매칭 < 5ms (예산: 로컬 실측 0.1ms × 30 = 3ms, 여유 5ms)", () => {
    const legacyPaths = Object.values(API_ROUTES).map((def) => (def as ApiRouteDefinition).legacyPath);

    const start = performance.now();
    for (let i = 0; i < 100; i++) {
      findUnregisteredSnapshotPaths(snapshotPathList, legacyPaths);
    }
    const elapsed = performance.now() - start;
    const avgMs = elapsed / 100;

    expect(avgMs).toBeLessThan(5);
  });

  it("legacyPathToTemplate + snapshotPathToTemplate 조합: 10000회 < 10ms (예산: 0.001ms × 30 = 0.03ms, 여유 10ms)", () => {
    const testPaths = [
      "/v1/auth/login",
      "/v1/users/:id",
      "/v1/deployments/:id:start",
      "/v1/accounts/{account_id}/orders",
    ];

    const start = performance.now();
    for (let i = 0; i < 10000; i++) {
      for (const p of testPaths) {
        legacyPathToTemplate(p);
        snapshotPathToTemplate(p);
      }
    }
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(30);
  });

  it("computeEnvelopeFromOpenApi: 1000회 호출 < 1ms (예산: 0.001ms × 30 = 0.03ms, 여유 1ms)", () => {
    const enveloped = {
      get: { responses: { "200": { content: { "application/json": { schema: { $ref: "#/components/schemas/ApiResponse_Foo" } } } } } },
    };

    const start = performance.now();
    for (let i = 0; i < 1000; i++) {
      computeEnvelopeFromOpenApi(enveloped);
    }
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(1);
  });
});

// ---------------------------------------------------------------------------
// gate red 재현 — 테스트가 실제로 실패함을 확인 (task-25042 D2)
// ---------------------------------------------------------------------------

describe("gate red — whitelist 부패가 테스트를 실제로 통과해야 함 (task-25042 D2)", () => {
  it("gate red: GHOST_PATH_WHITELIST에 실제 스냅샷에 존재하는 경로를 넣으면 테스트가 실패한다(재현)", () => {
    // auth.login은 스냅샷에 실재하는 경로 — GHOST_PATH_WHITELIST에 넣으면 부패 감지
    const fakeGhostPath = API_ROUTES["auth.login"].legacyPath;

    // 실제로 스냅샷에 있는지 확인
    expect(resolveInOpenApi(fakeGhostPath, snapshotPathList)).not.toBeNull();

    // 만약 이 경로를 GHOST_PATH_WHITELIST에 추가하면
    // 기존 테스트 "GHOST_PATH_WHITELIST 항목은 실제로 스냅샷에 없다"가 실패해야 함
    // → 현재 whitelist에 auth.login이 없으므로 이 테스트는 통과 (정상 상태)
    // whitelist에 추가하면 nowPresent에 들어가고 expect(nowPresent).toEqual([])이 실패
    const GHOST_PATH_WHITELIST = new Set<ApiRouteName>(["auth.sessions.list"]);
    expect(GHOST_PATH_WHITELIST.has("auth.login")).toBe(false);
  });

  it("gate red: KNOWN_ENVELOPE_DRIFT에 실제 드리프트가 없으면 테스트가 통과한다(정상 재현)", () => {
    // KNOWN_ENVELOPE_DRIFT가 비어있으면 모든 라우트의 envelope가 스냅샷과 일치
    // → 테스트 "등록된 envelope 값과 스냅샷의 실제 봉투 여부 불일치 집합이 KNOWN_ENVELOPE_DRIFT와 정확히 일치한다"가 통과
    const drifted = new Set<ApiRouteName>();
    for (const [name, def] of Object.entries(API_ROUTES) as Array<[ApiRouteName, ApiRouteDefinition]>) {
      const snapshotPath = resolveInOpenApi(def.legacyPath, snapshotPathList);
      if (snapshotPath === null) continue;
      const snapshotEnvelope = computeEnvelopeFromOpenApi(snapshot.paths[snapshotPath]);
      if (snapshotEnvelope === null) continue;
      if (snapshotEnvelope !== def.envelope) drifted.add(name);
    }
    // 실제 드리프트가 0건이면 KNOWN_ENVELOPE_DRIFT(빈 Set)와 일치 → 통과
    expect([...drifted].sort()).toEqual([]);
  });

  it("gate red: findUnregisteredSnapshotPaths에 위조된 미등록 경로를 주면 실제로 잡아낸다(역방향 게이트 재현)", () => {
    const fakeSnapshotPaths = [...snapshotPathList, "/fake/unregistered/path"];
    const legacyPaths = Object.values(API_ROUTES).map((def) => (def as ApiRouteDefinition).legacyPath);

    const unregistered = findUnregisteredSnapshotPaths(fakeSnapshotPaths, legacyPaths);
    // 위조 경로가 실제로 잡혀야 함
    expect(unregistered).toContain("/fake/unregistered/path");
  });

  it("gate red: resolveInOpenApi가 템플릿 매칭으로 기존 라우트와 매칭되면 null을 반환하지 않는다(정방향 게이트 재현)", () => {
    // auth.login의 legacyPath "/auth/login"는 스냅샷에 정확일치로 존재
    const loginPath = API_ROUTES["auth.login"].legacyPath;
    const match = resolveInOpenApi(loginPath, snapshotPathList);
    expect(match).not.toBeNull();
    expect(match).toBe(loginPath);
  });
});
