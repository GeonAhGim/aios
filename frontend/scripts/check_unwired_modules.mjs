#!/usr/bin/env node
// Unwired-module ratchet (task-2027): packages/chart-engine/src/index.ts exports
// modules that apps/web/src never actually imports — the "built it, screen never
// calls it" defect behind CH-4b/CH-15b/CH-16b/CH-18c (task-2001/2002/2012/2013,
// docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.11 CH-14~19).
// apps/web deliberately imports chart-engine submodules by deep path
// ("@aios/chart-engine/src/<module>") instead of the barrel — see the comments atop
// ChartToolbar.tsx/ChartPage.tsx (vendor klinecharts breaks apps/web's strict
// tsconfig through the barrel) — so wiring is detected at that same granularity.
// Ratchet policy mirrors check_frontend_file_size.mjs: a module already unwired at
// baseline time stays allowed (its own wiring task owns the fix); any module not in
// the baseline that shows up unwired is a new regression and fails the build.
// Shrinking the baseline (a module got wired) is reported but never auto-applied —
// a human commits the smaller baseline once the wiring task lands.
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, relative, resolve, sep, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED_SEGMENTS = new Set(["vendor", "node_modules", "dist"]);

function toPosix(p) {
  return p.split(sep).join("/");
}

// `export { internal as public } from "./mod"` — callers import "public", so the
// name after "as" is what maps to the module. `import { public as local } from`
// — "public" (before "as") is what must match an export map key; "local" is just
// the caller's variable name and is irrelevant here.
function stripAlias(name, side) {
  const idx = name.indexOf(" as ");
  if (idx === -1) return name.trim();
  return (side === "after" ? name.slice(idx + 4) : name.slice(0, idx)).trim();
}

function splitSymbols(block, side) {
  return block
    .split(",")
    .map((s) => stripAlias(s.replace(/\n/g, " "), side))
    .filter(Boolean);
}

/**
 * Parses `export [type] { A, B as C } from "./mod";` statements out of a barrel
 * file. Returns the set of exported module specifiers (relative, no extension,
 * "./" stripped) and a symbol -> module map for resolving barrel imports.
 */
export function parseIndexExports(content) {
  const modules = new Set();
  const symbolToModule = new Map();
  const re = /export\s+(?:type\s+)?\{([\s\S]*?)\}\s+from\s+["']([^"']+)["']/g;
  let m;
  while ((m = re.exec(content)) !== null) {
    const modulePath = m[2].replace(/^\.\//, "");
    modules.add(modulePath);
    for (const symbol of splitSymbols(m[1], "after")) {
      if (!symbolToModule.has(symbol)) symbolToModule.set(symbol, modulePath);
    }
  }
  return { modules, symbolToModule };
}

function resolveSpecifier(fromDir, specifier) {
  const base = resolve(fromDir, specifier);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * chart-engine's index.ts re-exports grouped sub-barrels via `export * from
 * "./index/<group>"` (P6 300-line ratchet split) instead of naming every module
 * directly, so the exported-module set can't be read off index.ts's own text —
 * `export *` has no symbol list to parse. This walks that re-export graph (index.ts
 * -> ./index/<group>.ts -> ../core/<module>) and resolves every `export {...} from`
 * it finds to a module id relative to indexPath's own directory, so ids match the
 * "core/renderer" form used by baseline entries and by findWiredModules' deep-import
 * scan regardless of how many barrel hops away the export statement actually lives.
 */
export function collectExportedModules(indexPath) {
  const rootDir = dirname(resolve(indexPath));
  const modules = new Set();
  const symbolToModule = new Map();
  const seen = new Set();

  function visit(absPath) {
    if (seen.has(absPath)) return;
    seen.add(absPath);
    const content = readFileSync(absPath, "utf-8");
    const dir = dirname(absPath);

    const starRe = /export\s+\*\s+from\s+["']([^"']+)["']/g;
    let m;
    while ((m = starRe.exec(content)) !== null) {
      const target = resolveSpecifier(dir, m[1]);
      if (target) visit(target);
    }

    // Unlike `export *` above, this doesn't require the target file to exist: the
    // module id is derived from the specifier path alone (matches parseIndexExports'
    // behavior of trusting the text), so a barrel can name a module that hasn't
    // landed yet without the resolver silently dropping it from the exported set.
    const namedRe = /export\s+(?:type\s+)?\{([\s\S]*?)\}\s+from\s+["']([^"']+)["']/g;
    while ((m = namedRe.exec(content)) !== null) {
      const moduleId = toPosix(relative(rootDir, resolve(dir, m[2]))).replace(/\.tsx?$/, "");
      modules.add(moduleId);
      for (const symbol of splitSymbols(m[1], "after")) {
        if (!symbolToModule.has(symbol)) symbolToModule.set(symbol, moduleId);
      }
    }
  }

  visit(resolve(indexPath));
  return { modules, symbolToModule };
}

/**
 * Scans one consumer file's source for chart-engine wiring: deep submodule imports
 * (`@aios/chart-engine/src/<module>`) count directly; barrel imports
 * (`@aios/chart-engine`) resolve their named symbols through `symbolToModule`.
 */
export function findWiredModules(content, symbolToModule) {
  const wired = new Set();
  const deepRe = /from\s+["']@aios\/chart-engine\/src\/([^"']+)["']/g;
  let m;
  while ((m = deepRe.exec(content)) !== null) {
    wired.add(m[1].replace(/\.tsx?$/, ""));
  }
  const barrelRe = /import\s+(?:type\s+)?\{([\s\S]*?)\}\s+from\s+["']@aios\/chart-engine["']/g;
  while ((m = barrelRe.exec(content)) !== null) {
    for (const symbol of splitSymbols(m[1], "before")) {
      const mod = symbolToModule.get(symbol);
      if (mod) wired.add(mod);
    }
  }
  return wired;
}

export function isProductionSource(relPosixPath) {
  const base = relPosixPath.split("/").pop();
  if (/\.(test|spec)\.tsx?$/.test(base)) return false;
  if (relPosixPath.split("/").some((seg) => EXCLUDED_SEGMENTS.has(seg))) return false;
  return /\.tsx?$/.test(base);
}

function walk(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (EXCLUDED_SEGMENTS.has(entry.name)) continue;
      walk(full, out);
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
}

/** Returns every unwired module (exported by the barrel, never imported by any production file under `webSrc`). */
export function findUnwiredModulesFromExports(modules, symbolToModule, webSrc) {
  const wired = new Set();
  const files = [];
  walk(webSrc, files);
  for (const full of files) {
    const relPosix = toPosix(relative(webSrc, full));
    if (!isProductionSource(relPosix)) continue;
    const content = readFileSync(full, "utf-8");
    for (const mod of findWiredModules(content, symbolToModule)) wired.add(mod);
  }
  return [...modules].filter((mod) => !wired.has(mod)).sort();
}

/** Same as findUnwiredModulesFromExports, but parses a flat index.ts string directly (no `export *` resolution). */
export function findUnwiredModules(indexContent, webSrc) {
  const { modules, symbolToModule } = parseIndexExports(indexContent);
  return findUnwiredModulesFromExports(modules, symbolToModule, webSrc);
}

/**
 * Ratchet rule: an unwired module missing from `baseline` is a violation (a new
 * export landed with nothing calling it, or an existing wiring got removed).
 * A baseline entry no longer unwired is reported as improvable, never auto-applied.
 */
export function checkRatchet(unwiredModules, baseline) {
  const unwiredSet = new Set(unwiredModules);
  const baselineSet = new Set(baseline);
  const violations = unwiredModules.filter((mod) => !baselineSet.has(mod));
  const improvable = baseline.filter((mod) => !unwiredSet.has(mod));
  return { violations, improvable };
}

function loadBaseline(path) {
  const parsed = JSON.parse(readFileSync(path, "utf-8"));
  return parsed.modules ?? [];
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--index") args.index = argv[++i];
    else if (arg === "--web-src") args.webSrc = argv[++i];
    else if (arg === "--baseline") args.baseline = argv[++i];
  }
  return args;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { index, webSrc, baseline } = parseArgs(argv);
  const indexPath = index ? resolve(index) : join(frontendRoot, "packages", "chart-engine", "src", "index.ts");
  const webSrcPath = webSrc ? resolve(webSrc) : join(frontendRoot, "apps", "web", "src");
  const baselinePath = baseline ? resolve(baseline) : join(frontendRoot, "scripts", "unwired-modules-baseline.json");

  const { modules, symbolToModule } = collectExportedModules(indexPath);
  const unwired = findUnwiredModulesFromExports(modules, symbolToModule, webSrcPath);
  const baselineModules = loadBaseline(baselinePath);
  const { violations, improvable } = checkRatchet(unwired, baselineModules);

  for (const mod of violations) {
    console.log(`FAIL: ${mod} — chart-engine exports it but apps/web never imports it (not in baseline)`);
  }
  for (const mod of improvable) {
    console.log(`INFO: ${mod} is wired now — baseline entry can be removed by hand`);
  }
  if (violations.length > 0) {
    console.log(`unwired-modules ratchet: ${violations.length} violation(s)`);
    return 1;
  }
  console.log("unwired-modules ratchet: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
