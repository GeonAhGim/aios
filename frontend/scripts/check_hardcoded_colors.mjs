#!/usr/bin/env node
// UX-3 (task-2687) hardcoded-color ratchet: apps/web/src and packages/ui-web/src must
// route color through the design tokens (apps/web/src/index.css `@theme` block +
// packages/ui-web/src/chartPalette.ts's CATEGORICAL_PALETTE/DIVERGING_*) instead of
// literal hex/rgb/hsl values, so both ship in the dark/light theme swap that
// packages/ui-web/src/theme.ts drives (a literal color can't follow `[data-theme]`).
// Same ratchet policy as check_i18n_literals.mjs/check_frontend_file_size.mjs
// (ADR-2026-09-10-C): files already carrying a literal are pinned in the baseline and
// only fail on growth; a brand-new file with a literal fails outright. Detection is a
// source-text heuristic, not a CSS/JS parser: it flags `#RGB`/`#RRGGBB`/`#RRGGBBAA` hex
// codes and `rgb()`/`rgba()`/`hsl()`/`hsla()` calls whose arguments are numeric/percent
// literals only -- calls built from a template-literal or variable expression (e.g.
// `` rgb(${r}, ${g}, ${b}) `` derived from a token, see SweepResultsPage.tsx's
// heatColor) are intentionally not literals and must not be flagged.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED_SEGMENTS = new Set(["vendor", "node_modules", "dist", "coverage"]);
const INCLUDED_EXTENSIONS = [".ts", ".tsx", ".css"];
// 토큰 정의 파일 자체는 리터럴 색상을 담는 게 정상이다(다른 모든 파일이 이 값을
// var()/import로 참조해야 한다는 게 이 랫칫의 요지) -- repo-root 기준 상대경로.
const TOKEN_SOURCE_FILES = new Set([
  "apps/web/src/index.css",
  "packages/ui-web/src/chartPalette.ts",
  "packages/ui-web/src/canvasColorFallbacks.ts",
]);

const HEX_COLOR = /#[0-9a-fA-F]{3,8}\b/g;
const FUNCTIONAL_COLOR = /\b(?:rgba?|hsla?)\(\s*[\d.%\s,]+\)/g;

const DEFAULT_ROOTS = ["apps/web/src", "packages/ui-web/src"];

function toPosix(relPath) {
  return relPath.split(sep).join("/");
}

export function isExcluded(relPosixPath) {
  if (TOKEN_SOURCE_FILES.has(relPosixPath)) return true;
  const segments = relPosixPath.split("/");
  if (segments.some((seg) => EXCLUDED_SEGMENTS.has(seg))) return true;
  const base = segments[segments.length - 1];
  if (/\.test\.tsx?$/.test(base)) return true;
  return !INCLUDED_EXTENSIONS.some((ext) => base.endsWith(ext));
}

/** Counts hex + functional-notation color literals in `source`. */
export function countHardcodedColors(source) {
  const hex = source.match(HEX_COLOR)?.length ?? 0;
  const functional = source.match(FUNCTIONAL_COLOR)?.length ?? 0;
  return hex + functional;
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

/** Scans each of `roots` (absolute dirs) and returns [{ path, count }] relative to `base`. */
export function scanFiles(roots, base) {
  const files = [];
  for (const root of roots) walk(root, files);
  const found = [];
  for (const full of files) {
    const relPosix = toPosix(relative(base, full));
    if (isExcluded(relPosix)) continue;
    const content = readFileSync(full, "utf-8");
    const count = countHardcodedColors(content);
    if (count > 0) found.push({ path: relPosix, count });
  }
  found.sort((a, b) => a.path.localeCompare(b.path));
  return found;
}

/**
 * Ratchet rule (mirrors check_i18n_literals.mjs's checkRatchet exactly): a file not in
 * `baseline` with any hardcoded color is a violation. A baselined file is a violation
 * only if its count grew past the pin. A file that dropped below its baseline is
 * reported as improvable, never auto-applied.
 */
export function checkRatchet(scanned, baseline) {
  const violations = [];
  const improvable = [];
  const scannedByPath = new Map(scanned.map((s) => [s.path, s.count]));
  for (const { path, count } of scanned) {
    const base = baseline[path];
    if (base === undefined) {
      violations.push({ path, count, reason: "new hardcoded color literal(s), not in the UX-3 baseline" });
      continue;
    }
    if (count > base) {
      violations.push({ path, count, reason: `grew past baseline (${base} -> ${count})` });
    } else if (count < base) {
      improvable.push({ path, count, baseline: base });
    }
  }
  for (const path of Object.keys(baseline)) {
    if (!scannedByPath.has(path)) improvable.push({ path, count: 0, baseline: baseline[path] });
  }
  return { violations, improvable };
}

function loadBaseline(path) {
  const parsed = JSON.parse(readFileSync(path, "utf-8"));
  return parsed.files ?? {};
}

function parseArgs(argv) {
  const args = { roots: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--root") args.roots.push(argv[++i]);
    else if (arg === "--base") args.base = argv[++i];
    else if (arg === "--baseline") args.baseline = argv[++i];
    else if (arg === "--dump") args.dump = true;
  }
  return args;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { roots, base, baseline, dump } = parseArgs(argv);
  const effectiveBase = base ? resolve(base) : frontendRoot;
  const effectiveRoots =
    roots.length > 0 ? roots.map((r) => resolve(r)) : DEFAULT_ROOTS.map((r) => join(frontendRoot, ...r.split("/")));
  const baselinePath = baseline ? resolve(baseline) : join(frontendRoot, "scripts", "hardcoded-colors-baseline.json");

  const scanned = scanFiles(effectiveRoots, effectiveBase);

  if (dump) {
    for (const { path, count } of scanned) console.log(`${path}: ${count}`);
    return 0;
  }

  const baselineMap = loadBaseline(baselinePath);
  const { violations, improvable } = checkRatchet(scanned, baselineMap);

  for (const v of violations) {
    console.log(`FAIL: ${v.path}:${v.count} — ${v.reason}`);
  }
  for (const i of improvable) {
    console.log(`INFO: ${i.path}:${i.count} is below baseline ${i.baseline} — baseline can be lowered (commit it by hand)`);
  }
  if (violations.length > 0) {
    console.log(
      `hardcoded-colors ratchet: ${violations.length} violation(s) — use a design token (apps/web/src/index.css @theme, or @aios/ui-web's CATEGORICAL_PALETTE/DIVERGING_*) instead of a literal color`,
    );
    return 1;
  }
  console.log("hardcoded-colors ratchet: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
