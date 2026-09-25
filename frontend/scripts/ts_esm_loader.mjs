/**
 * Shared Node ESM resolver hook so a plain `.mjs`/`.ts` entry point run under
 * `node --experimental-strip-types` (stable Node 22 built-in type stripping)
 * can import this repo's `.ts` sources by their bare, extension-less
 * specifier (the TS-source convention used throughout `frontend/packages/*`)
 * without a bundler, a compiled `dist/`, or a new devDependency (ts-node/tsx
 * are not installed). Node's own ESM resolver requires an explicit
 * extension on relative specifiers; this hook appends `.ts` when the bare
 * specifier has none and the `.ts` file exists, then defers to the default
 * resolver/loader for everything else (including the actual type-stripping
 * transform, which Node performs based on the `--experimental-strip-types`
 * flag, not on anything this hook does).
 *
 * Originally written for packages/chart-engine/bench/density_bench.mjs
 * (task-1958/1959, CH-19b); promoted here (task-4973) so
 * packages/api-client/bench/idempotencyScan_bench.mjs can reuse the same
 * hook instead of duplicating it per package.
 */
import { existsSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export async function resolve(specifier, context, nextLoad) {
  const isRelative = specifier.startsWith("./") || specifier.startsWith("../");
  const hasExtension = /\.[a-zA-Z0-9]+$/.test(specifier);
  if (isRelative && !hasExtension) {
    const parentDir = dirname(fileURLToPath(context.parentURL));
    const candidate = resolvePath(parentDir, `${specifier}.ts`);
    if (existsSync(candidate)) {
      return nextLoad(pathToFileURL(candidate).href, context);
    }
  }
  return nextLoad(specifier, context);
}
