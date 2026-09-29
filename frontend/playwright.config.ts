import { defineConfig, devices } from "@playwright/test";

// H-7b(ADR-2026-09-09-B): 프론트 Playwright 스모크. 백엔드는 띄우지 않고
// support/mockBackend.ts가 page.route로 고정 픽스처를 되돌린다 — 실 서버
// 왕복 없이 apps/web 서버만 기동한다. chromium만 쓴다(스모크 목적, DoD).
export default defineConfig({
  testDir: "./e2e",
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
  // task-8572 근본 정정: `vite dev`는 각 워커가 처음 여는 라우트의 청크를 그때그때
  // esbuild로 JIT 컴파일한다 — 여러 워커가 서로 다른 무거운 라우트(예: executions)를
  // 동시에 처음 열면 단일 dev 서버 프로세스의 변환 큐가 밀려 특정 assertion만
  // 이유 없이 10초 안에 안 끝나는 산발적 CI 적색이 났다(esc-ci-e2e.json 이력 —
  // bisect가 무관한 커밋에 착지할 만큼 원인이 코드가 아니라 dev 서버 JIT 경합이었다).
  // 빌드된 정적 산출물을 preview로 서빙하면 테스트 실행 중 컴파일이 전혀 없어
  // 이 경합 자체가 사라진다 — 빌드 1회 비용(약 1분)을 webServer 기동에 선불로 낸다.
  webServer: {
    command: "npm run build --workspace=apps/web && npm run preview --workspace=apps/web -- --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
