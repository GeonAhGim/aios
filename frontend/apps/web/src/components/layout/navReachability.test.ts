import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  ALL_NAV_ITEMS,
  extractRegisteredRoutes,
  findUnreachableRoutes,
  NAV_EXCLUSION_WHITELIST,
} from "./navItems";

// task-2380 FE-NAV-1. router.tsx는 FE-OPS 체인(task-2336~2347)이 순차 점유
// 중이라 이 파일은 그것을 읽기만 한다(decision §C).
const ROUTER_PATH = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "router.tsx");

describe("extractRegisteredRoutes/findUnreachableRoutes — 스캐너 자체 검증", () => {
  it("파싱 가능한 라우트가 0건이면 빈 배열이 아니라 throw한다(I-10)", () => {
    expect(() => extractRegisteredRoutes("export const router = createBrowserRouter([]);")).toThrow();
  });

  it("반증 fixture: 가짜 router 소스에 nav 링크 없는 등록 라우트를 넣으면 그 경로를 정확히 지목하며 FAIL한다", () => {
    const fakeRouterSource = `
      export const router = createBrowserRouter([
        { path: "/dashboard", element: protect(<DashboardPage />) },
        { path: "/ghost-page-without-nav-link", element: protect(<GhostPage />) },
        { path: "/admin/ghost-admin-page", element: protectAdmin(<GhostAdminPage />) },
      ]);
    `;
    const routes = extractRegisteredRoutes(fakeRouterSource);
    const navPaths = new Set(["/dashboard"]);
    const unreachable = findUnreachableRoutes(routes, navPaths, new Set());

    expect(unreachable).toEqual(["/ghost-page-without-nav-link", "/admin/ghost-admin-page"]);
  });

  it("파라미터·redirect·비인증 라우트는 nav 링크 없이도 위반으로 잡히지 않는다", () => {
    const fakeRouterSource = `
      export const router = createBrowserRouter([
        { path: "/", element: <Navigate to="/dashboard" replace /> },
        { path: "/login", element: <LoginPage /> },
        { path: "/items/:itemId", element: protect(<ItemPage />) },
      ]);
    `;
    const routes = extractRegisteredRoutes(fakeRouterSource);
    const unreachable = findUnreachableRoutes(routes, new Set(), new Set());

    expect(unreachable).toEqual([]);
  });

  it("항상 빈 배열을 돌려주는 무력화된 스캐너는 이 테스트로 걸러진다", () => {
    const alwaysEmptyScanner = () => [] as string[];
    const fakeRouterSource = `
      export const router = createBrowserRouter([
        { path: "/dashboard", element: protect(<DashboardPage />) },
        { path: "/ghost-page-without-nav-link", element: protect(<GhostPage />) },
      ]);
    `;
    const routes = extractRegisteredRoutes(fakeRouterSource);
    const realResult = findUnreachableRoutes(routes, new Set(["/dashboard"]), new Set());
    const neuteredResult = alwaysEmptyScanner();

    expect(realResult).not.toEqual(neuteredResult);
    expect(realResult).toEqual(["/ghost-page-without-nav-link"]);
  });
});

describe("NAV_EXCLUSION_WHITELIST — 래칫", () => {
  it("항목마다 빈 문자열이 아닌 사유가 있다", () => {
    for (const exclusion of NAV_EXCLUSION_WHITELIST) {
      expect(exclusion.reason.trim().length).toBeGreaterThan(0);
    }
  });

  it("항목 수는 착수 시점 실측치(4)를 넘지 않는다 — 늘리려면 이 테스트도 같이 고쳐야 한다", () => {
    expect(NAV_EXCLUSION_WHITELIST.length).toBeLessThanOrEqual(4);
  });
});

describe("router.tsx 도달성 — 등록된 라우트는 전부 nav 링크나 화이트리스트에 있다", () => {
  it("navPaths ∪ whitelistPaths가 인증 라우트 전체를 커버한다", () => {
    const routerSource = readFileSync(ROUTER_PATH, "utf-8");
    const routes = extractRegisteredRoutes(routerSource);
    const navPaths = new Set(ALL_NAV_ITEMS.map((item) => item.to));
    const whitelistPaths = new Set(NAV_EXCLUSION_WHITELIST.map((exclusion) => exclusion.path));

    const unreachable = findUnreachableRoutes(routes, navPaths, whitelistPaths);

    expect(unreachable).toEqual([]);
  });
});
