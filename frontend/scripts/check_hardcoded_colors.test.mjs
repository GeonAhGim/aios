// UX-3 (task-2687) concrete-rejection tests for the hardcoded-colors ratchet, mirroring
// check_i18n_literals.test.mjs's pattern per that task's precedent: a scanner that only
// asserts the passing path is not acceptable -- these must show the exit code and the
// offending "path:count" line for each failure mode (ADR-2026-09-09-C D2: negative
// >=3, failure injection 1, perf assertion 1, gate-red repro 1).
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isExcluded, countHardcodedColors, checkRatchet } from "./check_hardcoded_colors.mjs";

const SCRIPT = join(import.meta.dirname, "check_hardcoded_colors.mjs");

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "ux3-hardcoded-colors-"));
  const src = join(root, "src");
  mkdirSync(src, { recursive: true });
  return { root, src };
}

function writeBaseline(root, files) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify({ files }));
  return path;
}

function run(root, roots, baselinePath, extraArgs = []) {
  const rootArgs = roots.flatMap((r) => ["--root", r]);
  try {
    const stdout = execFileSync(
      process.execPath,
      [SCRIPT, ...rootArgs, "--base", root, "--baseline", baselinePath, ...extraArgs],
      { encoding: "utf-8" },
    );
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout, stderr: err.stderr, error: err };
  }
}

// -- unit-level detector behavior ------------------------------------------------

test("countHardcodedColors flags hex literals of 3/6/8 digits", () => {
  assert.equal(countHardcodedColors("const c = '#fff';"), 1);
  assert.equal(countHardcodedColors("const c = '#334455';"), 1);
  assert.equal(countHardcodedColors("const c = '#33445566';"), 1);
});

test("countHardcodedColors flags literal rgb()/rgba()/hsl()/hsla() but not template-literal/variable-built ones", () => {
  assert.equal(countHardcodedColors("grid: 'rgba(42, 38, 32, 0.6)'"), 1);
  assert.equal(countHardcodedColors("grid: 'hsl(200, 50%, 40%)'"), 1);
  // heatColor()-shape (SweepResultsPage.tsx): built from variables via template
  // literal -- already token-derived upstream, must not be flagged.
  assert.equal(countHardcodedColors("return `rgb(${r}, ${g}, ${b})`;"), 0);
});

test("countHardcodedColors sums hex and functional-notation hits in one file", () => {
  assert.equal(countHardcodedColors("const a = '#fff'; const b = 'rgba(0, 0, 0, 0.5)';"), 2);
});

test("isExcluded filters vendor/, node_modules/, dist/, coverage/, *.test.tsx, non-ts(x)/css files, and the token source files", () => {
  assert.equal(isExcluded("apps/web/src/vendor/klinecharts/Big.tsx"), true);
  assert.equal(isExcluded("apps/web/src/components/Comp.test.tsx"), true);
  assert.equal(isExcluded("apps/web/src/dist/Gen.tsx"), true);
  assert.equal(isExcluded("apps/web/src/components/Comp.md"), true);
  assert.equal(isExcluded("apps/web/src/index.css"), true);
  assert.equal(isExcluded("packages/ui-web/src/chartPalette.ts"), true);
  assert.equal(isExcluded("packages/ui-web/src/canvasColorFallbacks.ts"), true);
  assert.equal(isExcluded("apps/web/src/components/Comp.tsx"), false);
  assert.equal(isExcluded("packages/ui-web/src/Button.tsx"), false);
});

// -- checkRatchet policy ----------------------------------------------------------

test("checkRatchet: new file with a hardcoded color not in baseline is a violation", () => {
  const { violations } = checkRatchet([{ path: "src/Fresh.tsx", count: 1 }], {});
  assert.equal(violations.length, 1);
  assert.match(violations[0].reason, /not in the UX-3 baseline/);
});

test("checkRatchet: baseline-tracked file that grew is a violation; steady or shrunk is not", () => {
  const baseline = { "src/A.tsx": 2, "src/B.tsx": 2 };
  const { violations, improvable } = checkRatchet(
    [
      { path: "src/A.tsx", count: 3 },
      { path: "src/B.tsx", count: 2 },
    ],
    baseline,
  );
  assert.equal(violations.length, 1);
  assert.equal(violations[0].path, "src/A.tsx");
  assert.equal(improvable.length, 0);
});

// -- end-to-end CLI: gate-red repro + negative rejection paths --------------------

test("[gate-red repro] rejects a brand-new file that hardcodes a hex color", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export const Fresh = () => <div style={{ color: "#ff00ff" }} />;\n`);
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, [src], baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Fresh\.tsx:1/);
    assert.match(stdout, /hardcoded-colors ratchet: 1 violation/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] rejects a baseline-tracked file whose color count grew past its pin", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "Tracked.tsx"),
      `export const c1 = "#111111";\nexport const c2 = "#222222";\n`,
    );
    const baseline = writeBaseline(root, { "src/Tracked.tsx": 1 });
    const { status, stdout } = run(root, [src], baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Tracked\.tsx:2 — grew past baseline \(1 -> 2\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] does not flag a baseline-tracked file that stayed at its pinned count", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Steady.tsx"), `export const c = "#abcabc";\n`);
    const baseline = writeBaseline(root, { "src/Steady.tsx": 1 });
    const { status, stdout } = run(root, [src], baseline);
    assert.equal(status, 0);
    assert.doesNotMatch(stdout, /FAIL/);
    assert.match(stdout, /OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] exclusion rules apply during a scan: vendor/test/dist/coverage/token-source files over budget do not fail", () => {
  const { root, src } = makeWorkspace();
  try {
    mkdirSync(join(src, "vendor"), { recursive: true });
    mkdirSync(join(src, "dist"), { recursive: true });
    mkdirSync(join(src, "coverage"), { recursive: true });
    writeFileSync(join(src, "vendor", "Big.tsx"), `export const c = "#123456";\n`);
    writeFileSync(join(src, "Comp.test.tsx"), `export const c = "#123456";\n`);
    writeFileSync(join(src, "dist", "Gen.tsx"), `export const c = "#123456";\n`);
    writeFileSync(join(src, "coverage", "report.css"), `.x { color: #123456; }\n`);
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, [src], baseline);
    assert.equal(status, 0);
    assert.match(stdout, /OK/);
    assert.doesNotMatch(stdout, /FAIL/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("scans multiple --root directories at once (apps/web/src + packages/ui-web/src shape)", () => {
  const { root, src } = makeWorkspace();
  const webSrc = join(src, "apps-web");
  const uiWebSrc = join(src, "ui-web");
  try {
    mkdirSync(webSrc, { recursive: true });
    mkdirSync(uiWebSrc, { recursive: true });
    writeFileSync(join(webSrc, "Page.tsx"), `export const c = "#654321";\n`);
    writeFileSync(join(uiWebSrc, "Widget.tsx"), `export const c = "#123abc";\n`);
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, [webSrc, uiWebSrc], baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: .*apps-web\/Page\.tsx:1/);
    assert.match(stdout, /FAIL: .*ui-web\/Widget\.tsx:1/);
    assert.match(stdout, /hardcoded-colors ratchet: 2 violation/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- failure injection --------------------------------------------------------------

test("[failure injection] a corrupt baseline file fails loudly instead of silently passing everything", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export const c = "#00ff00";\n`);
    const baselinePath = join(root, "baseline.json");
    writeFileSync(baselinePath, "{ not valid json");
    const result = run(root, [src], baselinePath);
    // A corrupt baseline must not be treated as "no baseline" (status 0 / silent
    // pass) -- it has to surface as a hard failure so a broken baseline commit is
    // caught immediately rather than quietly disabling the whole ratchet.
    assert.notEqual(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- performance assertion -----------------------------------------------------------

test("[perf budget] scanning 300 synthetic files stays under 2000ms", () => {
  const { root, src } = makeWorkspace();
  try {
    for (let i = 0; i < 300; i += 1) {
      writeFileSync(
        join(src, `Gen${i}.tsx`),
        `export function Gen${i}() {\n  return <div style={{ color: "#ab${String(i).padStart(4, "0")}" }} />;\n}\n`,
      );
    }
    const baseline = writeBaseline(root, {});
    const started = Date.now();
    const { status } = run(root, [src], baseline);
    const elapsed = Date.now() - started;
    assert.equal(status, 1); // every generated file is a fresh violation
    assert.ok(elapsed < 2000, `expected scan of 300 files under 2000ms, took ${elapsed}ms`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
