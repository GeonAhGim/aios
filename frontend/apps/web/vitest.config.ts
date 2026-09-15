import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config.ts";

// task-1968: CI/공유 머신에서 병행 실행되는 다른 워커(백엔드 pytest 등)와의 CPU 경합으로
// 개별 테스트가 기본 5000ms 안에 못 끝나 무관한 스위트 전반에서 동시다발로 timeout
// 났음을 실측 재현(동일 신호가 ChartPage/IndicatorPicker/InstrumentsPage/SessionsPage
// 등 서로 무관한 파일에 걸쳐 발생)으로 확인했다. 로직 결함이 아니라 여유 시간 부족이므로
// 타임아웃을 넉넉히 늘리고, 이 스위트 자체가 공유 머신에서 코어를 과점하지 않도록
// 워커 수도 제한한다.
// task-3460: the performance-assertion tests read `VITEST_COVERAGE` (see
// src/test/perfBudget.ts) to widen their wall-clock budgets only when V8
// coverage instrumentation is active. The config file runs in the main process
// where the CLI flags are visible, so the signal is derived here and handed to
// every worker through `test.env`.
const coverageRequested = process.argv.some(
  (arg) => arg === "--coverage" || arg === "--coverage.enabled" || arg === "--coverage.enabled=true",
);

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      setupFiles: ["./src/test/setup.ts"],
      testTimeout: 20000,
      hookTimeout: 20000,
      maxWorkers: 4,
      env: { VITEST_COVERAGE: coverageRequested ? "1" : "0" },
    },
  }),
);
