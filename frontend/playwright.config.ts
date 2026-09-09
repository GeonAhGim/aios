import { defineConfig, devices } from "@playwright/test";

// H-7b(ADR-2026-09-09-B): 프론트 Playwright 스모크. 백엔드는 띄우지 않고
// support/mockBackend.ts가 page.route로 고정 픽스처를 되돌린다 — 실 서버
// 왕복 없이 apps/web dev 서버만 기동한다. chromium만 쓴다(스모크 목적, DoD).
export default defineConfig({
  testDir: "./e2e",
  // Vite dev 서버가 처음 뜬 뒤 router.tsx의 정적 import(모든 라우트 컴포넌트를
  // 한 번에 로드) 때문에 콜드 스타트 첫 내비게이션이 느리다 — 웜 캐시(2회차부터
  // node_modules/.vite)에선 10초 안팎이지만, 콜드에서는 40~50초까지 걸릴 수 있어
  // 기본 30초보다 넉넉히 둔다(그래도 10분 상한 안에 4건이 들어온다).
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev --workspace=apps/web",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
