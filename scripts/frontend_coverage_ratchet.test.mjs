// task-3182: scripts/frontend_coverage_ratchet.mjs had no test file at all,
// not even happy-path coverage. Exercises baseline init, drops within/beyond
// tolerance, ratchet-up, custom tolerance, and missing/malformed
// summary/baseline cases by actually running the process and checking exit
// code, stdout, and the resulting baseline file contents.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SCRIPT = join(import.meta.dirname, "frontend_coverage_ratchet.mjs");

function makeWorkspace() {
  return mkdtempSync(join(tmpdir(), "coverage-ratchet-"));
}

function writeSummary(root, pct) {
  const path = join(root, "coverage-summary.json");
  writeFileSync(path, JSON.stringify({ total: { lines: { pct } } }));
  return path;
}

function run(root, args) {
  try {
    const stdout = execFileSync(process.execPath, [SCRIPT, ...args], {
      cwd: root,
      encoding: "utf-8",
    });
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout };
  }
}

test("initializes baseline when none exists", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 87.5);
    const baseline = join(root, "coverage-baseline.txt");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 0);
    assert.match(stdout, /BASELINE 초기화/);
    assert.equal(readFileSync(baseline, "utf-8").trim(), "87.50");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when coverage drops beyond tolerance", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 89.0);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: 커버리지 하락/);
    assert.equal(readFileSync(baseline, "utf-8").trim(), "90.00");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("passes when coverage drop stays within default tolerance", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 89.7);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 0);
    assert.match(stdout, /OK: 커버리지 89\.70%/);
    assert.equal(readFileSync(baseline, "utf-8").trim(), "90.00");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("ratchets baseline up when coverage rises", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 91.25);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 0);
    assert.match(stdout, /OK: 커버리지 상승, baseline 갱신 90\.00% -> 91\.25%/);
    assert.equal(readFileSync(baseline, "utf-8").trim(), "91.25");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("respects a custom --tolerance", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 88.0);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, [
      "--coverage-summary",
      summary,
      "--baseline",
      baseline,
      "--tolerance",
      "3",
    ]);
    assert.equal(status, 0);
    assert.match(stdout, /OK: 커버리지 88\.00%/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when coverage-summary.json is missing", () => {
  const root = makeWorkspace();
  try {
    const summary = join(root, "does-not-exist.json");
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: coverage-summary\.json 없음/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when coverage-summary.json is malformed", () => {
  const root = makeWorkspace();
  try {
    const summary = join(root, "coverage-summary.json");
    writeFileSync(summary, "{ not valid json");
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: coverage-summary\.json 파싱 실패/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when coverage-summary.json lacks total.lines.pct", () => {
  const root = makeWorkspace();
  try {
    const summary = join(root, "coverage-summary.json");
    writeFileSync(summary, JSON.stringify({ total: {} }));
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "90.00\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: coverage-summary\.json: total\.lines\.pct 없음\/숫자 아님/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when baseline file is empty", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 90.0);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "  \n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: baseline 파일이 비어 있음/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fails when baseline value is not numeric", () => {
  const root = makeWorkspace();
  try {
    const summary = writeSummary(root, 90.0);
    const baseline = join(root, "coverage-baseline.txt");
    writeFileSync(baseline, "not-a-number\n");
    const { status, stdout } = run(root, ["--coverage-summary", summary, "--baseline", baseline]);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: baseline 값이 숫자가 아님/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
