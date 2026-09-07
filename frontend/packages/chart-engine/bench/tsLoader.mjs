/**
 * CH-19b — minimal Node ESM resolver hook so `density_bench.mjs` can import
 * this package's `.ts` sources directly (via `node --experimental-strip-types`,
 * stable Node 22 built-in type stripping) without a bundler, a compiled
 * `dist/`, or a new devDependency (ts-node/tsx are not installed and adding
 * one would be a new OSS import for a leaf that does not need it). Node's
 * own ESM resolver requires an explicit extension on relative specifiers;
 * this hook appends `.ts` when the bare specifier has none and the `.ts`
 * file exists, then defers to the default resolver/loader for everything
 * else (including the actual type-stripping transform, which Node performs
 * based on the `--experimental-strip-types` flag, not on anything this hook
 * does). No external dependency, no code transformation of its own.
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
