import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ADMIN_NAV_ITEMS, ALL_NAV_ITEMS, extractRegisteredRoutes } from "./navItems";

// task-2380 FE-NAV-1. navReachability.test.ts는 "등록된 라우트 → nav 링크"
// 방향을 검사한다. 이 파일은 반대 방향("nav 링크 → 등록된 라우트")을 검사해
// 존재하지 않는 경로로 가는 죽은 링크를 잡는다.
const ROUTER_PATH = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "router.tsx");

describe("ALL_NAV_ITEMS — 데이터 정합성", () => {
  it("모든 to 값이 router.tsx에 실제로 등록된 경로다(죽은 링크 0건)", () => {
    const routerSource = readFileSync(ROUTER_PATH, "utf-8");
    const registeredPaths = new Set(extractRegisteredRoutes(routerSource).map((route) => route.routePath));

    const deadLinks = ALL_NAV_ITEMS.map((item) => item.to).filter((to) => !registeredPaths.has(to));

    expect(deadLinks).toEqual([]);
  });

  it("to 값에 중복이 없다", () => {
    const toValues = ALL_NAV_ITEMS.map((item) => item.to);
    expect(new Set(toValues).size).toBe(toValues.length);
  });

  it("링크는 최소 12개 이상이다(현재 3건 → 확장, task-2380 DoD)", () => {
    expect(ALL_NAV_ITEMS.length).toBeGreaterThanOrEqual(12);
  });

  it("/admin 하위 링크는 ADMIN_NAV_ITEMS에만 있다(권한 그룹 밖으로 새지 않는다)", () => {
    const nonAdminAdminLinks = ALL_NAV_ITEMS.filter(
      (item) => !ADMIN_NAV_ITEMS.includes(item) && item.to.startsWith("/admin"),
    );
    expect(nonAdminAdminLinks).toEqual([]);
  });
});
