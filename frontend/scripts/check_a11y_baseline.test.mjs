// UX-4 (task-2688) concrete-rejection tests for the a11y baseline ratchet + contrast
// gate, mirroring check_hardcoded_colors.test.mjs's pattern per that task's precedent
// (ADR-2026-09-09-C D2: negative >=3, failure injection 1, perf assertion 1, gate-red
// repro 1): a scanner that only asserts the passing path is not acceptable -- these
// must show the exit code and the offending output line for each failure mode.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  checkContrast,
  checkRatchet,
  contrastRatio,
  countA11yIssues,
  findA11yIssues,
  findTagEnd,
  isExcluded,
  parseThemeBlocks,
  WCAG_AA_TEXT,
} from "./check_a11y_baseline.mjs";

const SCRIPT = join(import.meta.dirname, "check_a11y_baseline.mjs");

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "ux4-a11y-baseline-"));
  const src = join(root, "src");
  mkdirSync(src, { recursive: true });
  return { root, src };
}

function writeBaseline(root, files) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify({ files }));
  return path;
}

const VALID_CSS = `@theme {\n  --color-bg: #0d0c0a;\n  --color-fg: #f2ede0;\n}\n[data-theme="light"] {\n  --color-bg: #faf8f2;\n  --color-fg: #241f14;\n}\n`;

function run(root, roots, baselinePath, cssPath, extraArgs = []) {
  const rootArgs = roots.flatMap((r) => ["--root", r]);
  try {
    const stdout = execFileSync(
      process.execPath,
      [SCRIPT, ...rootArgs, "--base", root, "--baseline", baselinePath, "--css", cssPath, ...extraArgs],
      { encoding: "utf-8" },
    );
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout, stderr: err.stderr, error: err };
  }
}

function writeValidCss(root) {
  const path = join(root, "theme.css");
  writeFileSync(path, VALID_CSS);
  return path;
}

// -- unit-level detector behavior ---------------------------------------------------

test("findA11yIssues flags <img> without alt/aria-hidden as image-alt", () => {
  const issues = findA11yIssues(`<img src="x.png" />`);
  assert.deepEqual(issues, [{ rule: "image-alt", tagName: "img" }]);
});

test("findA11yIssues does not flag <img> that has alt or aria-hidden", () => {
  assert.equal(findA11yIssues(`<img src="x.png" alt="설명" />`).length, 0);
  assert.equal(findA11yIssues(`<img src="x.png" aria-hidden />`).length, 0);
});

test("findA11yIssues flags a bare <input> as label, but not one with aria-label/id or a {...spread}", () => {
  assert.deepEqual(findA11yIssues(`<input type="text" />`), [{ rule: "label", tagName: "input" }]);
  assert.equal(findA11yIssues(`<input id="x" type="text" />`).length, 0);
  assert.equal(findA11yIssues(`<input aria-label="검색" />`).length, 0);
  assert.equal(findA11yIssues(`<input {...rest} />`).length, 0);
});

test("findA11yIssues flags role=\"dialog\" without an accessible name as dialog-name", () => {
  assert.deepEqual(findA11yIssues(`<div role="dialog"><p>hi</p></div>`), [
    { rule: "dialog-name", tagName: "div" },
  ]);
  assert.equal(findA11yIssues(`<div role="dialog" aria-labelledby="t"><p>hi</p></div>`).length, 0);
});

test("findTagEnd skips '>' inside arrow functions and >= comparisons to find the real tag close", () => {
  const source = `<input onChange={(e) => set(e.target.value)} disabled={n >= 5} id="x" />`;
  const end = findTagEnd(source, 1);
  assert.equal(source.slice(0, end + 1), source);
});

test("countA11yIssues sums multiple rule hits in one file", () => {
  assert.equal(countA11yIssues(`<img src="a" /><input type="text" />`), 2);
});

test("isExcluded filters vendor/node_modules/dist/coverage, *.test.tsx, and non-.tsx files", () => {
  assert.equal(isExcluded("apps/web/src/vendor/Big.tsx"), true);
  assert.equal(isExcluded("apps/web/src/components/Comp.test.tsx"), true);
  assert.equal(isExcluded("apps/web/src/lib/util.ts"), true);
  assert.equal(isExcluded("apps/web/src/components/Comp.tsx"), false);
});

// -- checkRatchet policy -------------------------------------------------------------

test("checkRatchet: new file with an issue not in baseline is a violation", () => {
  const { violations } = checkRatchet([{ path: "src/Fresh.tsx", count: 1 }], {});
  assert.equal(violations.length, 1);
  assert.match(violations[0].reason, /not in the UX-4 baseline/);
});

test("checkRatchet: baseline-tracked file that grew is a violation; steady is not", () => {
  const baseline = { "src/A.tsx": 1 };
  const { violations } = checkRatchet([{ path: "src/A.tsx", count: 2 }], baseline);
  assert.equal(violations.length, 1);
  assert.match(violations[0].reason, /grew past baseline \(1 -> 2\)/);
  const steady = checkRatchet([{ path: "src/A.tsx", count: 1 }], baseline);
  assert.equal(steady.violations.length, 0);
});

// -- contrast gate --------------------------------------------------------------------

test("contrastRatio: black vs white is 21:1", () => {
  assert.ok(Math.abs(contrastRatio("#000000", "#ffffff") - 21) < 0.1);
});

test("parseThemeBlocks reads dark tokens from @theme and light overrides from [data-theme=light]", () => {
  const blocks = parseThemeBlocks(VALID_CSS);
  assert.equal(blocks.dark.bg, "#0d0c0a");
  assert.equal(blocks.dark.fg, "#f2ede0");
  assert.equal(blocks.light.bg, "#faf8f2");
  assert.equal(blocks.light.fg, "#241f14");
});

test("[negative] checkContrast flags a foreground/background pair below the 4.5:1 AA text threshold", () => {
  const violations = checkContrast({ "fg-muted": "#8a7e5f", bg: "#faf8f2" });
  assert.equal(violations.length, 1);
  assert.equal(violations[0].ratio < WCAG_AA_TEXT, true);
});

test("[negative] checkContrast does not flag a pair that clears the threshold", () => {
  assert.equal(checkContrast({ fg: "#f2ede0", bg: "#0d0c0a" }).length, 0);
});

test("[negative] checkContrast skips tokens absent from the theme (partial palettes don't crash)", () => {
  assert.deepEqual(checkContrast({ fg: "#f2ede0" }), []);
});

// -- end-to-end CLI: gate-red repro + failure injection + perf ----------------------

test("[gate-red repro] rejects a brand-new file with an unlabeled input not in the baseline", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export const Fresh = () => <input type="text" />;\n`);
    const baseline = writeBaseline(root, {});
    const css = writeValidCss(root);
    const { status, stdout } = run(root, [src], baseline, css);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Fresh\.tsx:1/);
    assert.match(stdout, /a11y baseline: 1 violation/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] rejects a baseline-tracked file whose issue count grew past its pin", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Tracked.tsx"), `export const c = () => (<><input /><input /></>);\n`);
    const baseline = writeBaseline(root, { "src/Tracked.tsx": 1 });
    const css = writeValidCss(root);
    const { status, stdout } = run(root, [src], baseline, css);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Tracked\.tsx:2 — grew past baseline \(1 -> 2\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] passes a clean tree against an empty baseline with a valid theme", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Clean.tsx"), `export const Clean = () => <img src="x" alt="d" />;\n`);
    const baseline = writeBaseline(root, {});
    const css = writeValidCss(root);
    const { status, stdout } = run(root, [src], baseline, css);
    assert.equal(status, 0);
    assert.match(stdout, /a11y baseline: OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[failure injection] a corrupt baseline file fails loudly instead of silently passing everything", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export const c = () => <input type="text" />;\n`);
    const baselinePath = join(root, "baseline.json");
    writeFileSync(baselinePath, "{ not valid json");
    const css = writeValidCss(root);
    const result = run(root, [src], baselinePath, css);
    // 손상된 baseline은 "baseline 없음"(status 0)으로 취급되면 안 된다 -- 전체
    // ratchet이 조용히 꺼진 채로 커밋되는 사고를 막으려면 즉시 시끄럽게 실패해야 한다.
    assert.notEqual(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[failure injection] an unparseable theme CSS path fails the gate instead of silently skipping the contrast check", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Clean.tsx"), `export const c = () => <img src="x" alt="d" />;\n`);
    const baseline = writeBaseline(root, {});
    const missingCssPath = join(root, "does-not-exist.css");
    const result = run(root, [src], baseline, missingCssPath);
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /unable to read\/parse theme CSS/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[gate-red repro] the contrast gate fails when a theme token pair is below the WCAG AA threshold", () => {
  const { root, src } = makeWorkspace();
  try {
    const baseline = writeBaseline(root, {});
    const badCss = join(root, "bad.css");
    // 실제 회귀 이전의 라이트 테마 fg-muted/bg 조합(3.78:1)을 재현한다.
    writeFileSync(
      badCss,
      `@theme {\n  --color-bg: #0d0c0a;\n  --color-fg: #f2ede0;\n}\n[data-theme="light"] {\n  --color-bg: #faf8f2;\n  --color-fg-muted: #8a7e5f;\n}\n`,
    );
    const { status, stdout } = run(root, [src], baseline, badCss);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: contrast\[light\] --color-fg-muted vs --color-bg = 3\.7\d:1/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- performance assertion -----------------------------------------------------------

test("[perf budget] scanning 300 synthetic files stays under 2000ms", () => {
  const { root, src } = makeWorkspace();
  try {
    for (let i = 0; i < 300; i += 1) {
      writeFileSync(join(src, `Gen${i}.tsx`), `export function Gen${i}() {\n  return <input type="text" />;\n}\n`);
    }
    const baseline = writeBaseline(root, {});
    const css = writeValidCss(root);
    const started = Date.now();
    const { status } = run(root, [src], baseline, css);
    const elapsed = Date.now() - started;
    assert.equal(status, 1); // every generated file is a fresh violation
    assert.ok(elapsed < 2000, `expected scan of 300 files under 2000ms, took ${elapsed}ms`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
