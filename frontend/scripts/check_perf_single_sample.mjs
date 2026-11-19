#!/usr/bin/env node
// PERF-FE (task-25947) single-sample performance assertion ratchet:
// *.test.ts/tsx/mjs files that measure time with performance.now() and assert
// toBeLessThan / toBeLessThanOrEqual without using bestOfMs / bestOfMsAsync
// are flagged. A single wall-clock sample is unreliable on a shared host
// (CI contention, JIT warm-up, GC pauses). The fix is to use bestOfMs from
// test/perfBudget so the budget is judged on the fastest of several runs.
//
// Ratchet policy (same as i18n-literals / file-size):
//   - Files already in the baseline are allowed as long as they keep using
//     bestOfMs. If a baseline file starts violating, it is reported and must
//     be removed from the baseline by hand.
//   - A brand-new file not in the baseline that violates is a hard failure
//     (new file should not introduce this pattern).
//   - The baseline only shrinks: removing a file from it confirms the fix.

import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED_SEGMENTS = new Set([
  "vendor",
  "node_modules",
  "dist",
  "i18n",
  "fixtures",
  "__snapshots__",
]);
const INCLUDED_EXTENSIONS = [".ts", ".tsx", ".mjs"];
const TEST_RE = /\.test\.(tsx|ts|mjs)$/;

function toPosix(relPath) {
  return relPath.split(sep).join("/");
}

export function isExcluded(relPosixPath) {
  const segments = relPosixPath.split("/");
  if (segments.some((seg) => EXCLUDED_SEGMENTS.has(seg))) return true;
  const base = segments[segments.length - 1];
  if (!TEST_RE.test(base)) return true;
  return !INCLUDED_EXTENSIONS.some((ext) => base.endsWith(ext));
}

/**
 * Heuristic: a test file has the "single-sample wall-clock assertion" pattern
 * when it (a) calls performance.now(), (b) asserts with toBeLessThan or
 * toBeLessThanOrEqual, and (c) does NOT import/use bestOfMs / bestOfMsAsync.
 */
export function hasSingleSamplePerfAssert(source) {
  const hasPerfNow = /performance\.now\(\)/.test(source);
  const hasToBeLess = /toBeLessThan(OrEqual)?\(/.test(source);
  const hasBestOf = /bestOfMs/.test(source);
  return hasPerfNow && hasToBeLess && !hasBestOf;
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

/** Scans `root` (absolute dir) and returns [relative paths] of violating files. */
export function scanFiles(root, base) {
  const files = [];
  walk(root, files);
  const violations = [];
  for (const full of files) {
    const relPosix = toPosix(relative(base, full));
    if (isExcluded(relPosix)) continue;
    const content = readFileSync(full, "utf-8");
    if (hasSingleSamplePerfAssert(content)) {
      violations.push(relPosix);
    }
  }
  violations.sort();
  return violations;
}

/**
 * Ratchet: a file not in baseline with violations is a hard failure.
 * A file in baseline that still violates is OK (already pinned) but the
 * user is notified to remove it from baseline after fixing.
 * A file in baseline that is no longer found is reported as fixed.
 */
export function checkRatchet(violations, baselinePaths) {
  const newFiles = [];
  const baselineStillViolating = [];
  const baselineSet = new Set(baselinePaths);

  for (const v of violations) {
    if (!baselineSet.has(v)) {
      newFiles.push(v);
    } else {
      baselineStillViolating.push(v);
    }
  }
  return { newFiles, baselineStillViolating };
}

function loadBaseline(path) {
  const parsed = JSON.parse(readFileSync(path, "utf-8"));
  return Array.isArray(parsed) ? parsed : [];
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--root") args.root = argv[++i];
    else if (arg === "--base") args.base = argv[++i];
    else if (arg === "--baseline") args.baseline = argv[++i];
  }
  return args;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { root, base, baseline } = parseArgs(argv);
  const effectiveBase = base ? resolve(base) : frontendRoot;
  const effectiveRoot = root ? resolve(root) : join(frontendRoot, "apps", "web", "src");
  const baselinePath = baseline
    ? resolve(baseline)
    : join(frontendRoot, "scripts", "perf-single-sample-baseline.json");

  const violations = scanFiles(effectiveRoot, effectiveBase);

  if (violations.length === 0) {
    console.log("perf-single-sample ratchet: OK (no violations found)");
    return 0;
  }

  const baselinePaths = loadBaseline(baselinePath);
  const { newFiles, baselineStillViolating } = checkRatchet(violations, baselinePaths);

  for (const f of newFiles) {
    console.log(`FAIL: ${f} — new file with single-sample performance assertion (use bestOfMs from test/perfBudget)`);
  }
  for (const f of baselineStillViolating) {
    console.log(`INFO: ${f} — baseline file still violates (remove from baseline after fixing)`);
  }
  if (newFiles.length > 0) {
    console.log(
      `perf-single-sample ratchet: ${newFiles.length} new violation(s) — ` +
        `use bestOfMs(bestOfMsAsync) from test/perfBudget instead of a single performance.now() sample`,
    );
    return 1;
  }
  console.log("perf-single-sample ratchet: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
