import { defineConfig } from "vitest/config";

// task-3460 CI-GREEN-2 (b): Vitest 4 removed the implicit npm/yarn/pnpm-workspace
// auto-discovery that older Vitest majors used when `vitest` ran from a monorepo
// root with no root config (previously also expressible via the now-removed
// `vitest.workspace.ts`). Without this file, `npx vitest run` from `frontend/`
// fell back to a single default project (environment "node", no setupFiles,
// no plugins) applied to every *.test.* file found repo-wide -- so apps/web's
// DOM component tests ran without jsdom and failed with
// `ReferenceError: document is not defined` (observed: 928 failed/1610 passed,
// 889 of them that exact error). Declaring `test.projects` is the Vitest 4
// replacement: each glob match with its own vite.config.*/vitest.config.*
// (apps/web) keeps that config (jsdom, setupFiles, plugins); matches with none
// (the packages/*, which are plain non-DOM unit tests) keep Vitest's defaults,
// exactly like running `vitest run` inside each package directly.
export default defineConfig({
  test: {
    projects: ["apps/*", "packages/*"],
  },
});
