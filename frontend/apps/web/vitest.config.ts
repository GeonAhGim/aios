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
      // task-3460 CI-GREEN-2 (b): the new root frontend/vitest.config.ts
      // `test.projects` combines this project with the packages/* projects
      // (which keep Vitest's default maxWorkers). Vitest 4 requires distinct
      // `sequence.groupOrder` for projects that disagree on pool settings
      // like maxWorkers -- this doesn't change what runs, only that this
      // project's group is scheduled separately from the default-pool group.
      sequence: { groupOrder: 1 },
      // task-3460: Node 22+ ships an experimental native `localStorage`/
      // `sessionStorage` Web Storage API on globalThis (flag `--webstorage`,
      // on by default; confirmed present through Node 26). It collides with
      // jsdom's own per-window Storage implementation -- `window.localStorage`
      // (and the bare global alias jsdom also sets) comes back `undefined`
      // instead of a Storage object, breaking every module that reads
      // `localStorage` at import time (e.g. `@aios/shared-hooks`'s
      // `useAuthStore`), independent of and in addition to the separate
      // `document is not defined` project-discovery issue this file's other
      // comment describes. `--no-experimental-webstorage` on each worker
      // restores jsdom's own Storage. Machines that provisioned this repo
      // under Node 22 (no `--webstorage` yet) never hit this.
      execArgv: ["--no-experimental-webstorage"],
    },
  }),
);
