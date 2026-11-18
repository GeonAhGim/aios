import { defineConfig, devices } from "@playwright/test";

import { UAT_WEB_URL } from "./e2e-uat/uatEnv";

// Real-stack UAT suite: targets an already running UAT stack (no mocks, no webServer).
export default defineConfig({
  testDir: "./e2e-uat",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [["list"]],
  use: {
    baseURL: UAT_WEB_URL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
