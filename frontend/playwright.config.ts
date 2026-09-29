import { createHash } from "node:crypto";
import { defineConfig, devices } from "@playwright/test";

// task-8753 근본 정정: 이 저장소는 worktree(frontend-1, ops-1, cto-config, ...)마다
// local_ci.py가 독립적으로 journeys/e2e 단계를 돈다. 고정 포트(5173)에
// `webServer.reuseExistingServer: !process.env.CI`(로컬 실행이라 항상 true)로
// 붙이면, 같은 호스트에서 동시에 도는 다른 worktree의 preview 서버(다른 커밋의
// 빌드 산출물)에 "재사용"으로 연결되거나, 그 서버가 자기 local_ci 종료로
// 죽는 순간 이쪽 스위트 중간에 ERR_CONNECTION_REFUSED가 난다(esc-ci-journeys.json —
// journey-j3 6단계 /alerts에서 관측). worktree 경로로 포트를 고유하게 파생시키고
// 항상 새 서버를 기동해(reuseExistingServer: false) 다른 worktree와 절대 겹치지
// 않게 한다 — 진짜 원인은 순서 의존이 아니라 여러 worktree가 같은 포트를 공유한
// 것이었다.
const WORKTREE_PORT =
  20000 +
  (parseInt(createHash("sha1").update(process.cwd()).digest("hex").slice(0, 4), 16) % 10000);
const BASE_URL = `http://localhost:${WORKTREE_PORT}`;

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
    baseURL: BASE_URL,
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
    command:
      "npm run build --workspace=apps/web && npm run preview --workspace=apps/web -- " +
      `--strictPort --port ${WORKTREE_PORT}`,
    url: BASE_URL,
    // task-8753: 항상 이 worktree 전용 서버를 새로 띄운다 -- reuseExistingServer(로컬은
    // 기본 true)를 켜면 다른 worktree가 우연히 같은 포트에 먼저 띄운 서버를 "재사용"으로
    // 잘못 붙잡을 여지가 있다. 포트가 worktree별로 이미 고유해졌으니 재사용 이점보다
    // 교차 오염 방지가 우선이다.
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
