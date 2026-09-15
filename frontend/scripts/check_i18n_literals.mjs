#!/usr/bin/env node
// UX-1 i18n literal ratchet (task-2685): apps/web/src must not grow new hardcoded
// user-facing strings once the i18n framework (catalog.ko, formatters) lands.
// UX-2 owns extracting the ~147 files that already have literals today, so this
// ratchet follows the same policy as check_frontend_file_size.mjs / check_unwired_modules.mjs
// (ADR-2026-09-10-C): files already over budget are pinned in the baseline and stay
// put as long as they don't grow more literals; a brand-new file or growth past the
// pinned count fails the build. Detection is a source-text heuristic, not a real JSX
// parser: it flags Hangul text appearing (a) as JSX text content between tags, after
// stripping `{expr}` interpolations, and (b) inside common user-facing string
// attributes (title/placeholder/alt/aria-label/label). It intentionally does not
// understand template literals or strings passed through variables -- a narrower net
// that stays false-positive-free is more useful as a CI gate than a wider one that
// needs constant suppressions.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED_SEGMENTS = new Set(["vendor", "node_modules", "dist", "i18n"]);
const INCLUDED_EXTENSIONS = [".ts", ".tsx"];
const HANGUL = /[가-힣]/;
const ATTR_NAMES = ["title", "placeholder", "alt", "aria-label", "label"];

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

/** Strips `{expr}` interpolations (single-level, non-nested -- good enough for JSX text runs). */
function stripInterpolations(text) {
  return text.replace(/\{[^{}]*\}/g, " ");
}

/** Counts JSX text-node spans (between `>` and `<`) that contain Hangul once interpolations are removed. */
export function countJsxTextLiterals(source) {
  let count = 0;
  const re = />([^<>]*)</g;
  let m;
  while ((m = re.exec(source)) !== null) {
    const stripped = stripInterpolations(m[1]);
    if (HANGUL.test(stripped)) count += 1;
  }
  return count;
}

/** Counts string-literal attribute values (title=, placeholder=, ...) that contain Hangul. */
export function countAttrLiterals(source) {
  let count = 0;
  const names = ATTR_NAMES.join("|");
  const re = new RegExp(`\\b(?:${names})\\s*=\\s*["']([^"']*)["']`, "g");
  let m;
  while ((m = re.exec(source)) !== null) {
    if (HANGUL.test(m[1])) count += 1;
  }
  return count;
}

export function countLiterals(source) {
  return countJsxTextLiterals(source) + countAttrLiterals(source);
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

/** Scans `root` (absolute dir) and returns [{ path, count }] relative to `base`, excluded files/zero-counts omitted. */
export function scanFiles(root, base) {
  const files = [];
  walk(root, files);
  const found = [];
  for (const full of files) {
    const relPosix = toPosix(relative(base, full));
    if (isExcluded(relPosix)) continue;
    const content = readFileSync(full, "utf-8");
    const count = countLiterals(content);
    if (count > 0) found.push({ path: relPosix, count });
  }
  found.sort((a, b) => a.path.localeCompare(b.path));
  return found;
}

/**
 * Ratchet rule: a file not in `baseline` with any literal is a violation (new
 * hardcoded string, should have used the i18n catalog instead). A file in `baseline`
 * is a violation only if its current count exceeds the baseline's pinned count
 * (growth). A file that dropped below its baseline is reported as improvable, never
 * auto-applied -- same policy as the file-size ratchet.
 */
export function checkRatchet(scanned, baseline) {
  const violations = [];
  const improvable = [];
  const scannedByPath = new Map(scanned.map((s) => [s.path, s.count]));
  for (const { path, count } of scanned) {
    const base = baseline[path];
    if (base === undefined) {
      violations.push({ path, count, reason: "new hardcoded literal(s), not in the UX-2 baseline" });
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
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--root") args.root = argv[++i];
    else if (arg === "--base") args.base = argv[++i];
    else if (arg === "--baseline") args.baseline = argv[++i];
    else if (arg === "--dump") args.dump = true;
  }
  return args;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { root, base, baseline, dump } = parseArgs(argv);
  const effectiveBase = base ? resolve(base) : frontendRoot;
  const effectiveRoot = root ? resolve(root) : join(frontendRoot, "apps", "web", "src");
  const baselinePath = baseline ? resolve(baseline) : join(frontendRoot, "scripts", "i18n-literals-baseline.json");

  const scanned = scanFiles(effectiveRoot, effectiveBase);

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
    console.log(`i18n-literals ratchet: ${violations.length} violation(s) — use the i18n catalog (see frontend/apps/web/src/i18n) instead of a hardcoded string`);
    return 1;
  }
  console.log("i18n-literals ratchet: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
