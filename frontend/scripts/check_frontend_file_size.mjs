#!/usr/bin/env node
// P6 file-size ratchet (task-2026): frontend/apps/web/src and frontend/packages/*/src
// must stay under 300 lines per .ts/.tsx file. Files already over budget are pinned
// in frontend-file-size-baseline.json; the ratchet only rejects growth past that
// pinned count (or a brand-new file starting over budget). Shrinking a baseline
// entry is never auto-applied — a human commits the lower number, same policy as
// packages/chart-engine/bench/densityRatchet.mjs and scripts/coverage_ratchet.py.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const LINE_LIMIT = 300;

const EXCLUDED_SEGMENTS = new Set(["vendor", "node_modules", "dist"]);
const INCLUDED_EXTENSIONS = [".ts", ".tsx"];

export function countLines(content) {
  const normalized = content.replace(/\r\n/g, "\n");
  if (normalized === "") return 0;
  return normalized.split("\n").length - (normalized.endsWith("\n") ? 1 : 0);
}

function toPosix(relPath) {
  return relPath.split(sep).join("/");
}

export function isExcluded(relPosixPath) {
  const segments = relPosixPath.split("/");
  if (segments.some((seg) => EXCLUDED_SEGMENTS.has(seg))) return true;
  const base = segments[segments.length - 1];
  if (/\.test\.tsx?$/.test(base)) return true;
  return !INCLUDED_EXTENSIONS.some((ext) => base.endsWith(ext));
}

function walk(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return; // root does not exist (e.g. a package with no src/) — nothing to scan
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

/** Scans `roots` (absolute dirs) and returns [{ path, lines }] relative to `base`, excluded files omitted. */
export function scanFiles(roots, base) {
  const found = [];
  for (const root of roots) {
    const files = [];
    walk(root, files);
    for (const full of files) {
      const relPosix = toPosix(relative(base, full));
      if (isExcluded(relPosix)) continue;
      const content = readFileSync(full, "utf-8");
      found.push({ path: relPosix, lines: countLines(content) });
    }
  }
  found.sort((a, b) => a.path.localeCompare(b.path));
  return found;
}

/**
 * Ratchet rule: a file not in `baseline` that exceeds LINE_LIMIT is a violation.
 * A file in `baseline` is a violation only if its current line count exceeds the
 * baseline's recorded count (growth), regardless of the limit — that lets a file
 * already over budget stay put as long as it does not grow further.
 * A file that shrank below its baseline is reported as improvable, not applied.
 */
export function checkRatchet(scanned, baseline) {
  const violations = [];
  const improvable = [];
  for (const { path, lines } of scanned) {
    const base = baseline[path];
    if (base === undefined) {
      if (lines > LINE_LIMIT) violations.push({ path, lines, reason: "new file over the 300-line limit" });
      continue;
    }
    if (lines > base) {
      violations.push({ path, lines, reason: `grew past baseline (${base} -> ${lines})` });
    } else if (lines < base) {
      improvable.push({ path, lines, baseline: base });
    }
  }
  return { violations, improvable };
}

function loadBaseline(path) {
  const parsed = JSON.parse(readFileSync(path, "utf-8"));
  return parsed.files ?? {};
}

function parseArgs(argv) {
  const roots = [];
  let baseline;
  let base;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--root") roots.push(argv[++i]);
    else if (arg === "--baseline") baseline = argv[++i];
    else if (arg === "--base") base = argv[++i];
  }
  return { roots, baseline, base };
}

function defaultRoots(frontendRoot) {
  const roots = [join(frontendRoot, "apps", "web", "src")];
  const packagesDir = join(frontendRoot, "packages");
  let packageNames = [];
  try {
    packageNames = readdirSync(packagesDir, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name);
  } catch {
    packageNames = [];
  }
  for (const name of packageNames) {
    roots.push(join(packagesDir, name, "src"));
  }
  return roots;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { roots, baseline, base } = parseArgs(argv);
  const effectiveBase = base ? resolve(base) : frontendRoot;
  const effectiveRoots = roots.length > 0 ? roots.map((r) => resolve(r)) : defaultRoots(frontendRoot);
  const baselinePath = baseline ? resolve(baseline) : join(frontendRoot, "scripts", "frontend-file-size-baseline.json");
  const baselineMap = loadBaseline(baselinePath);

  const scanned = scanFiles(effectiveRoots, effectiveBase);
  const { violations, improvable } = checkRatchet(scanned, baselineMap);

  for (const v of violations) {
    console.log(`FAIL: ${v.path}:${v.lines} — ${v.reason}`);
  }
  for (const i of improvable) {
    console.log(`INFO: ${i.path}:${i.lines} is below baseline ${i.baseline} — baseline can be lowered (commit it by hand)`);
  }
  if (violations.length > 0) {
    console.log(`P6 file-size ratchet: ${violations.length} violation(s)`);
    return 1;
  }
  console.log("P6 file-size ratchet: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
