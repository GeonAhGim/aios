import { defineConfig, devices } from "@playwright/test";

// task-25677: 실제 떠 있는 UAT 스택 대상 스모크 전용 설정. 기본 playwright.config.ts(./e2e,
// webServer 기동)와 분리되어 있으며 webServer를 띄우지 않는다 -- 스택(웹 18080, API 18000)은
// 외부에서 미리 기동돼 있어야 한다. 실행: `npm run test:uat` (frontend/ 에서).
const UAT_WEB_URL = process.env.UAT_WEB_URL ?? "http://127.0.0.1:18080";

export default defineConfig({
  testDir: "./e2e-uat",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: UAT_WEB_URL,
    // 실패 시에만 trace를 남긴다. 입력한 비밀번호가 trace 액션 로그에 남을 수 있으므로
    // 공유 전 trace 파일을 검토할 것(결과 보고 참조).
    trace: "retain-on-failure",
  },
  projects: [{ name: "uat-chromium", use: { ...devices["Desktop Chrome"] } }],
});
