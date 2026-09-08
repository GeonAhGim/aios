export interface NavItem {
  to: string;
  label: string;
}

// task-2380 FE-NAV-1. router.tsx에 등록된 라우트 중 최상위 화면 성격인 것들을
// 나열한다 — settings/admin 하위 라우트는 별도 그룹으로 갈라 AppShell이 권한
// 게이팅(admin) 여부에 따라 다르게 렌더한다.
export const MAIN_NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "대시보드" },
  { to: "/exchanges", label: "거래소" },
  { to: "/market/instruments", label: "종목" },
  { to: "/market/candles", label: "캔들" },
  { to: "/chart", label: "차트" },
  { to: "/strategy-builder", label: "전략편집기" },
  { to: "/scripts/editor", label: "스크립트편집기" },
  { to: "/marketplace", label: "마켓플레이스" },
  { to: "/executions", label: "실행제어판" },
  { to: "/portfolio", label: "포트폴리오" },
  { to: "/mandates", label: "위임장" },
  { to: "/reports", label: "보고서" },
  { to: "/wallet", label: "지갑" },
  { to: "/wallet/ledger", label: "지갑거래내역" },
  { to: "/wallet/payouts", label: "출금" },
  { to: "/alerts", label: "알림" },
  { to: "/system/paper-deployments", label: "페이퍼배포" },
  { to: "/approval-requests", label: "승인대기" },
];

export const SETTINGS_NAV_ITEMS: NavItem[] = [
  { to: "/settings/approval", label: "승인설정" },
  { to: "/settings/notifications", label: "알림설정" },
  { to: "/settings/sessions", label: "세션관리" },
  { to: "/settings/members", label: "멤버관리" },
  { to: "/settings/account", label: "계정삭제" },
];

// AppShell이 me.isPlatformAdmin일 때만 렌더한다(§3.5 fail-closed, AppShell.test.tsx).
export const ADMIN_NAV_ITEMS: NavItem[] = [
  { to: "/admin", label: "관리자" },
  { to: "/admin/system-status", label: "시스템상태" },
  { to: "/admin/verification-queue", label: "인증대기열" },
  { to: "/admin/disputes", label: "분쟁관리" },
  { to: "/admin/users", label: "사용자관리" },
  { to: "/admin/wallet-topups", label: "충전승인" },
  { to: "/admin/marketplace/platform-listings", label: "플랫폼목록" },
  { to: "/admin/approval-requests", label: "승인요청관리" },
  { to: "/admin/safety-controls", label: "안전통제" },
];

export const ALL_NAV_ITEMS: NavItem[] = [
  ...MAIN_NAV_ITEMS,
  ...SETTINGS_NAV_ITEMS,
  ...ADMIN_NAV_ITEMS,
];

export interface NavExclusion {
  path: string;
  reason: string;
}

// navReachability.test.ts가 검사하는 화이트리스트 — 라우트가 등록돼 있는데
// nav 링크가 없어도 되는 예외는 여기에 "왜"를 반드시 적고 추가한다. 항목 수
// 상한은 착수 시점 실측치(4)로 래칫한다: 라우트를 늘리면서 링크를 안 달면
// 이 배열도 같이 늘려야 하고, 그러면 navReachability.test.ts의 래칫 단언이
// FAIL한다 — 즉 사유 없이 조용히 넘어갈 수 없다.
export const NAV_EXCLUSION_WHITELIST: NavExclusion[] = [
  {
    path: "/onboarding/mfa-setup",
    reason:
      "ProtectedRoute(§onboarding)가 me.mfaEnabled=false일 때 강제 리다이렉트하는 1회성 전이 라우트 — 상시 내비게이션 진입점이 아니다.",
  },
  {
    path: "/onboarding/risk-assessment",
    reason:
      "ProtectedRoute가 리스크 적합성평가 미완료 시 강제 리다이렉트하는 1회성 전이 라우트 — 상시 내비게이션 진입점이 아니다.",
  },
  {
    path: "/marketplace/sell",
    reason:
      "MarketplaceBrowsePage 내부 '판매하기' Link로 이미 도달 가능한 컨텍스트형 액션 — 최상위 내비게이션에 중복 노출하지 않는다.",
  },
  {
    path: "/disputes/submit",
    reason:
      "ListingDetailPage 내부 분쟁 제기 Link로 이미 도달 가능한 컨텍스트형 액션 — 최상위 내비게이션에 중복 노출하지 않는다.",
  },
];

export interface ExtractedRoute {
  routePath: string;
  hasParam: boolean;
  isRedirect: boolean;
  isUnauthenticated: boolean;
}

// router.tsx 소스 문자열에서 `{ path: "...", element: ... }` 등록을 뽑아낸다.
// I-10: 정규식이 소스 포맷 변경을 못 따라가 0건이 나오면 "라우트가 없다"로
// 조용히 삼키지 않고 throw한다 — 그래야 스캐너가 늘 통과하는 무력화 상태를
// 구분해낼 수 있다(task-2034가 잡은 것과 같은 유형).
export function extractRegisteredRoutes(routerSource: string): ExtractedRoute[] {
  const entryPattern = /\{\s*path:\s*"([^"]+)"\s*,\s*element:\s*([\s\S]*?)\s*\}\s*,/g;
  const routes: ExtractedRoute[] = [];
  let match: RegExpExecArray | null;
  while ((match = entryPattern.exec(routerSource)) !== null) {
    const [, routePath, elementSrc] = match;
    routes.push({
      routePath,
      hasParam: routePath.includes(":"),
      isRedirect: elementSrc.includes("<Navigate"),
      isUnauthenticated: !elementSrc.includes("protect(") && !elementSrc.includes("protectAdmin("),
    });
  }
  if (routes.length === 0) {
    throw new Error(
      "router.tsx에서 등록된 라우트를 하나도 추출하지 못했다 — 정규식이 소스 포맷 변경을 못 따라갔을 수 있으니 빈 목록으로 넘기지 말고 확인하라.",
    );
  }
  return routes;
}

// 인증 라우트(param 없음·redirect 아님·비인증 라우트 아님) 중 navPaths에도
// whitelistPaths에도 없는 경로를 그대로 돌려준다 — 실패 메시지가 어떤
// 라우트가 문제인지 정확히 지목하도록 빈 배열이 아니라 위반 목록 자체를 반환한다.
export function findUnreachableRoutes(
  routes: ExtractedRoute[],
  navPaths: ReadonlySet<string>,
  whitelistPaths: ReadonlySet<string>,
): string[] {
  return routes
    .filter((route) => !route.hasParam && !route.isRedirect && !route.isUnauthenticated)
    .map((route) => route.routePath)
    .filter((routePath) => !navPaths.has(routePath) && !whitelistPaths.has(routePath));
}
