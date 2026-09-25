// task-2026: concrete-rejection tests for the P6 file-size ratchet. Per the task's
// DoD, a scanner that only asserts the passing path is not acceptable — these tests
// must show the exit code and the offending "path:lines" line for each failure mode.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isExcluded, countLines } from "./check_frontend_file_size.mjs";

const SCRIPT = join(import.meta.dirname, "check_frontend_file_size.mjs");

function linesOf(n) {
  return Array.from({ length: n }, (_, i) => `const line${i} = ${i};`).join("\n") + "\n";
}

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "p6-filesize-"));
  const src = join(root, "src");
  mkdirSync(src, { recursive: true });
  return { root, src };
}

function writeBaseline(root, files) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify({ files }));
  return path;
}

function run(root, src, baselinePath) {
  try {
    const stdout = execFileSync(process.execPath, [SCRIPT, "--root", src, "--base", root, "--baseline", baselinePath], {
      encoding: "utf-8",
    });
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout };
  }
}

test("countLines matches wc -l semantics (trailing newline not double-counted)", () => {
  assert.equal(countLines(""), 0);
  assert.equal(countLines("a\n"), 1);
  assert.equal(countLines("a\nb\nc\n"), 3);
  assert.equal(countLines("a\nb\nc"), 3); // no trailing newline still counts the last line
});

test("isExcluded filters vendor/, *.test.tsx, dist/, node_modules/, and non-ts(x) files", () => {
  assert.equal(isExcluded("src/vendor/klinecharts/big.ts"), true);
  assert.equal(isExcluded("src/routes/Comp.test.tsx"), true);
  assert.equal(isExcluded("src/dist/gen.ts"), true);
  assert.equal(isExcluded("src/node_modules/pkg/index.ts"), true);
  assert.equal(isExcluded("src/routes/Comp.md"), true);
  assert.equal(isExcluded("src/routes/Comp.tsx"), false);
});

test("rejects a brand-new file that exceeds the 500-line limit", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Fresh.tsx"), linesOf(501));
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Fresh\.tsx:501/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects a baseline-tracked file that grew by a single line", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Tracked.ts"), linesOf(305));
    const baseline = writeBaseline(root, { "src/Tracked.ts": 304 });
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: src\/Tracked\.ts:305/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("does not flag a baseline-tracked file that stayed at or shrank from its baseline", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(join(src, "Steady.ts"), linesOf(304));
    const baseline = writeBaseline(root, { "src/Steady.ts": 304 });
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 0);
    assert.doesNotMatch(stdout, /FAIL/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("exclusion rules actually apply during a scan: vendor/test/dist files over budget do not fail", () => {
  const { root, src } = makeWorkspace();
  try {
    mkdirSync(join(src, "vendor", "klinecharts"), { recursive: true });
    mkdirSync(join(src, "dist"), { recursive: true });
    writeFileSync(join(src, "vendor", "klinecharts", "Big.ts"), linesOf(501));
    writeFileSync(join(src, "Comp.test.tsx"), linesOf(501));
    writeFileSync(join(src, "dist", "Gen.ts"), linesOf(501));
    const baseline = writeBaseline(root, {});
    const { status, stdout } = run(root, src, baseline);
    assert.equal(status, 0);
    assert.match(stdout, /OK/);
    assert.doesNotMatch(stdout, /vendor/);
    assert.doesNotMatch(stdout, /Comp\.test\.tsx/);
    assert.doesNotMatch(stdout, /dist\/Gen\.ts/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
