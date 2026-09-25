import { defineConfig } from "vitest/config";

// task-3681: see packages/api-client/vitest.config.ts for why this file
// needs to exist (stops `vitest run` here from inheriting the root config's
// `test.projects`, which resolves relative to this cwd and finds nothing).
export default defineConfig({});
