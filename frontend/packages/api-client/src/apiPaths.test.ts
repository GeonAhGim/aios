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
