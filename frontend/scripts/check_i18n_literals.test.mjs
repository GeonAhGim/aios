// UX-1 (task-2685) concrete-rejection tests for the i18n-literals ratchet, mirroring
// check_frontend_file_size.test.mjs's pattern per that task's precedent: a scanner
// that only asserts the passing path is not acceptable -- these must show the exit
// code and the offending "path:count" line for each failure mode (ADR-2026-09-09-C
// D2: negative >=3, failure injection 1, perf assertion 1, gate-red repro 1).
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  isExcluded,
  countJsxTextLiterals,
  countAttrLiterals,
  countLiterals,
  checkRatchet,
  stripComments,
} from "./check_i18n_literals.mjs";

const SCRIPT = join(import.meta.dirname, "check_i18n_literals.mjs");

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "ux1-i18n-literals-"));
  const src = join(root, "src");
  mkdirSync(src, { recursive: true });
  return { root, src };
}

function writeBaseline(root, files) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify({ files }));
  return path;
}

function run(root, src, baselinePath, extraArgs = []) {
  try {
    const stdout = execFileSync(
      process.execPath,
      [SCRIPT, "--root", src, "--base", root, "--baseline", baselinePath, ...extraArgs],
      { encoding: "utf-8" },
    );
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout, stderr: err.stderr, error: err };
  }
}

// -- unit-level detector behavior ------------------------------------------------

test("countJsxTextLiterals flags plain Hangul JSX text but ignores ASCII-only and expression-only children", () => {
  assert.equal(countJsxTextLiterals("<p>다시 시도</p>"), 1);
  assert.equal(countJsxTextLiterals("<p>Retry</p>"), 0);
  assert.equal(countJsxTextLiterals("<span>{value}</span>"), 0);
});

test("countJsxTextLiterals strips {expr} interpolation and still flags residual Hangul text (ErrorMessage.tsx shape)", () => {
  // Regression fixture: the real violation in ErrorMessage.tsx is
  // `<p>지원코드: {traceId}</p>` -- Korean prefix text plus an interpolated variable.
  // A naive "any {" exclusion would miss this; the checker must strip only the
  // `{traceId}` span and still see the Hangul prefix.
  assert.equal(countJsxTextLiterals("<p>지원코드: {traceId}</p>"), 1);
});

test("countAttrLiterals flags Hangul in user-facing attributes but not other attributes", () => {
  assert.equal(countAttrLiterals('<input placeholder="이메일 입력" />'), 1);
  assert.equal(countAttrLiterals('<input className="text-sm" data-testid="이메일" />'), 0);
});

test("countLiterals sums JSX text and attribute hits", () => {
  assert.equal(countLiterals('<button title="확인">확인</button>'), 2);
});

test("countJsxTextLiterals ignores Hangul inside a // comment sitting between an unrelated `>` and `<` (EventLineageLookupPanel.tsx false positive)", () => {
  // A TS generic's closing `>` paired with a later `<` (e.g. useState<string>) can
  // span a code comment with no real JSX text in between -- the comment must not
  // be misread as a text node.
  const source = [
    "interface Props extends Foo<Bar> {}",
    "export function C(props: Props) {",
    "  // 한글 주석: 실제 JSX 텍스트가 아니다",
    "  const [x] = useState<string | null>(null);",
    "  return <p>Retry</p>;",
    "}",
  ].join("\n");
  assert.equal(countJsxTextLiterals(source), 0);
});

test("countJsxTextLiterals still flags real Hangul JSX text that follows a // comment", () => {
  const source = ["function C() {", "  // 주석", "  return <p>다시 시도</p>;", "}"].join("\n");
  assert.equal(countJsxTextLiterals(source), 1);
});

test("stripComments removes // and /* */ comments but leaves string/template literal contents untouched", () => {
  assert.equal(stripComments("a; // 한글 comment\nb;"), "a; \nb;");
  assert.equal(stripComments("/* 한글 block */ x"), " x");
  assert.equal(stripComments('const s = "http://not-a-comment";'), 'const s = "http://not-a-comment";');
});

test("isExcluded filters vendor/, i18n/, *.test.tsx, dist/, node_modules/, and non-ts(x) files", () => {
  assert.equal(isExcluded("src/vendor/klinecharts/Big.tsx"), true);
  assert.equal(isExcluded("src/i18n/catalog.ko.ts"), true);
  assert.equal(isExcluded("src/components/Comp.test.tsx"), true);
  assert.equal(isExcluded("src/dist/Gen.tsx"), true);
  assert.equal(isExcluded("src/node_modules/pkg/index.tsx"), true);
  assert.equal(isExcluded("src/components/Comp.md"), true);
  assert.equal(isExcluded("src/components/Comp.tsx"), false);
});

// -- checkRatchet policy ----------------------------------------------------------

test("checkRatchet: new file with literals not in baseline is a violation", () => {
  const { violations } = checkRatchet([{ path: "src/Fresh.tsx", count: 1 }], {});
  assert.equal(violations.length, 1);
  assert.match(violations[0].reason, /not in the UX-2 baseline/);
});

test("checkRatchet: baseline-tracked file that grew is a violation; steady or shrunk is not", () => {
  const baseline = { "src/A.tsx": 3, "src/B.tsx": 3 };
  const { violations, improvable } = checkRatchet(
    [
      { path: "src/A.tsx", count: 4 },
      { path: "src/B.tsx", count: 3 },
    ],
    baseline,
  );
  assert.equal(violations.length, 1);
  assert.equal(violations[0].path, "src/A.tsx");
  assert.equal(improvable.length, 0);
});

// -- end-to-end CLI: gate-red repro + negative rejection paths --------------------

test("[gate-red repro] rejects a brand-new file that hardcodes a Korean literal", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export function Fresh() {\n  return <p>다시 시도</p>;\n}\n`);
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Fresh\.tsx:1/);
    assert.match(stdout, /i18n-literals ratchet: 1 violation/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[gate-red repro] rejects a baseline-tracked file whose literal count grew past its pin", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "Tracked.tsx"),
      `export function Tracked() {\n  return (\n    <div>\n      <p>확인</p>\n      <p>취소</p>\n    </div>\n  );\n}\n`,
    );
    const baseline = writeBaseline(root, { "src/Tracked.tsx": 1 });
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Tracked\.tsx:2 — grew past baseline \(1 -> 2\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("does not flag a baseline-tracked file that stayed at its pinned count", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Steady.tsx"), `export function Steady() {\n  return <p>확인</p>;\n}\n`);
    const baseline = writeBaseline(root, { "src/Steady.tsx": 1 });
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 0);
    assert.doesNotMatch(stdout, /FAIL/);
    assert.match(stdout, /OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("exclusion rules actually apply during a scan: vendor/test/dist/i18n files over budget do not fail", () => {
  const { root, src } = makeWorkspace();
  try {
    mkdirSync(join(src, "vendor"), { recursive: true });
    mkdirSync(join(src, "dist"), { recursive: true });
    mkdirSync(join(src, "i18n"), { recursive: true });
    writeFileSync(join(src, "vendor", "Big.tsx"), `export const x = <p>확인</p>;\n`);
    writeFileSync(join(src, "Comp.test.tsx"), `export const x = <p>확인</p>;\n`);
    writeFileSync(join(src, "dist", "Gen.tsx"), `export const x = <p>확인</p>;\n`);
    writeFileSync(join(src, "i18n", "catalog.ko.ts"), `export const catalog = { ok: "확인" };\n`);
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 0);
    assert.match(stdout, /OK/);
    assert.doesNotMatch(stdout, /FAIL/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- failure injection --------------------------------------------------------------

test("[failure injection] a corrupt baseline file fails loudly instead of silently passing everything", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), `export const x = <p>확인</p>;\n`);
    const baselinePath = join(root, "baseline.json");
    writeFileSync(baselinePath, "{ not valid json");
    const result = run(root, src, baselinePath);
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
        `export function Gen${i}() {\n  return (\n    <div>\n      <p>내용 ${i}</p>\n      <span title="라벨 ${i}">텍스트</span>\n    </div>\n  );\n}\n`,
      );
    }
    const baseline = writeBaseline(root, {});
    const started = Date.now();
    const { status } = run(root, src, baseline);
    const elapsed = Date.now() - started;
    assert.equal(status, 1); // every generated file is a fresh violation
    assert.ok(elapsed < 2000, `expected scan of 300 files under 2000ms, took ${elapsed}ms`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
