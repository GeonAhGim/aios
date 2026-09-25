import { defineConfig } from "vitest/config";

// task-3681: without a package-local config, `vitest run` here (invoked via
// `npm run test --workspaces` from local_ci.py, task-2193) searches upward
// and picks up frontend/vitest.config.ts's `test.projects: ["apps/*",
// "packages/*"]`, which vitest then resolves relative to this package's own
// cwd -- neither glob matches anything from inside packages/api-client, so
// the run aborts with "No projects were found" before any test executes.
// A local config (even empty) stops the upward search, same as apps/web's
// own vitest.config.ts already does.
export default defineConfig({});
