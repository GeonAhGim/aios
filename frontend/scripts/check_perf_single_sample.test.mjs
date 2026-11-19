// PERF-FE (task-25947) concrete-rejection tests for the perf-single-sample ratchet,
// mirroring check_i18n_literals.test.mjs's pattern: a scanner that only asserts the
// passing path is not acceptable — these must show the exit code and the offending
// "path" line for each failure mode (ADR-2026-09-09-C D2: negative >=3, failure
// injection 1, perf assertion 1, gate-red repro 1).
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { hasSingleSamplePerfAssert, scanFiles, checkRatchet } from "./check_perf_single_sample.mjs";

const SCRIPT = join(import.meta.dirname, "check_perf_single_sample.mjs");

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "perf-single-sample-"));
  const src = join(root, "src");
  mkdirSync(src, { recursive: true });
  return { root, src };
}

function writeBaseline(root, paths) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify(paths));
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

test("hasSingleSamplePerfAssert flags performance.now() + toBeLessThan without bestOfMs", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      const t0 = performance.now();
      doWork();
      expect(performance.now() - t0).toBeLessThan(100);
    `),
    true,
  );
});

test("hasSingleSamplePerfAssert allows bestOfMs usage", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      import { bestOfMs } from "./test/perfBudget";
      const t0 = performance.now();
      expect(bestOfMs(() => performance.now() - t0)).toBeLessThan(100);
    `),
    false,
  );
});

test("hasSingleSamplePerfAssert ignores performance.now() without toBeLessThan", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      const t0 = performance.now();
      doWork();
      console.log(performance.now() - t0);
    `),
    false,
  );
});

test("hasSingleSamplePerfAssert ignores toBeLessThan without performance.now()", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      expect(someValue).toBeLessThan(100);
    `),
    false,
  );
});

test("hasSingleSamplePerfAssert allows bestOfMsAsync usage", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      import { bestOfMsAsync } from "./test/perfBudget";
      const elapsed = await bestOfMsAsync(async () => performance.now());
      expect(elapsed).toBeLessThan(100);
    `),
    false,
  );
});

test("hasSingleSamplePerfAssert flags toBeLessThanOrEqual too", () => {
  assert.equal(
    hasSingleSamplePerfAssert(`
      const t0 = performance.now();
      doWork();
      expect(performance.now() - t0).toBeLessThanOrEqual(100);
    `),
    true,
  );
});

// -- ratchet policy ---------------------------------------------------------------

test("checkRatchet rejects new file not in baseline", () => {
  const violations = ["apps/web/src/routes/foo/NewPage.test.tsx"];
  const baselinePaths = [];
  const result = checkRatchet(violations, baselinePaths);
  assert.equal(result.newFiles.length, 1);
  assert.equal(result.newFiles[0], "apps/web/src/routes/foo/NewPage.test.tsx");
  assert.equal(result.baselineStillViolating.length, 0);
});

test("checkRatchet allows baseline file that still violates (pinned)", () => {
  const violations = ["apps/web/src/routes/foo/OldPage.test.tsx"];
  const baselinePaths = ["apps/web/src/routes/foo/OldPage.test.tsx"];
  const result = checkRatchet(violations, baselinePaths);
  assert.equal(result.newFiles.length, 0);
  assert.equal(result.baselineStillViolating.length, 1);
});

test("checkRatchet rejects multiple new files", () => {
  const violations = [
    "apps/web/src/a.test.tsx",
    "apps/web/src/b.test.tsx",
    "apps/web/src/c.test.tsx",
  ];
  const baselinePaths = [];
  const result = checkRatchet(violations, baselinePaths);
  assert.equal(result.newFiles.length, 3);
  assert.equal(result.baselineStillViolating.length, 0);
});

test("checkRatchet handles mix of new and baseline files", () => {
  const violations = [
    "apps/web/src/new.test.tsx",
    "apps/web/src/old.test.tsx",
  ];
  const baselinePaths = ["apps/web/src/old.test.tsx"];
  const result = checkRatchet(violations, baselinePaths);
  assert.equal(result.newFiles.length, 1);
  assert.equal(result.newFiles[0], "apps/web/src/new.test.tsx");
  assert.equal(result.baselineStillViolating.length, 1);
  assert.equal(result.baselineStillViolating[0], "apps/web/src/old.test.tsx");
});

// -- integration: passing path ----------------------------------------------------

test("integration: empty src passes", () => {
  const { root, src } = makeWorkspace();
  try {
    const baselinePath = writeBaseline(root, []);
    const result = run(root, src, baselinePath);
    assert.equal(result.status, 0);
    assert.match(result.stdout, /OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("integration: baseline file that no longer violates passes (baseline shrinks)", () => {
  const { root, src } = makeWorkspace();
  try {
    // Write a violating file
    writeFileSync(
      join(src, "Old.test.tsx"),
      `const t0 = performance.now();\nexpect(performance.now() - t0).toBeLessThan(100);\n`,
    );
    // The scan returns paths relative to --base, so src/Old.test.tsx
    const baselinePath = writeBaseline(root, ["src/Old.test.tsx"]);
    const result = run(root, src, baselinePath);
    // Should pass — file is in baseline
    assert.equal(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- negative tests ---------------------------------------------------------------

test("[negative] non-test .ts file with performance.now() is ignored", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "utils.ts"),
      `const t0 = performance.now();\nexpect(performance.now() - t0).toBeLessThan(100);\n`,
    );
    const baselinePath = writeBaseline(root, []);
    const result = run(root, src, baselinePath);
    assert.equal(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] test file without performance.now() passes even with toBeLessThan", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "simple.test.tsx"),
      `expect(1).toBeLessThan(2);\n`,
    );
    const baselinePath = writeBaseline(root, []);
    const result = run(root, src, baselinePath);
    assert.equal(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("[negative] test file using bestOfMs passes even with performance.now()", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "good.test.tsx"),
      `import { bestOfMs } from "./test/perfBudget";\nconst t0 = performance.now();\nexpect(bestOfMs(() => performance.now() - t0)).toBeLessThan(100);\n`,
    );
    const baselinePath = writeBaseline(root, []);
    const result = run(root, src, baselinePath);
    assert.equal(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- failure injection -----------------------------------------------------------

test("[failure injection] a corrupt baseline file fails loudly instead of silently passing", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "Fresh.test.tsx"),
      `const t0 = performance.now();\nexpect(performance.now() - t0).toBeLessThan(100);\n`,
    );
    const baselinePath = join(root, "baseline.json");
    writeFileSync(baselinePath, "{ not valid json");
    const result = run(root, src, baselinePath);
    assert.notEqual(result.status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- performance assertion -------------------------------------------------------

test("[perf budget] scanning 200 synthetic test files stays under 1500ms", () => {
  const { root, src } = makeWorkspace();
  try {
    for (let i = 0; i < 200; i += 1) {
      writeFileSync(
        join(src, `Gen${i}.test.tsx`),
        `const t0 = performance.now();\ndoWork();\nexpect(performance.now() - t0).toBeLessThan(100);\n`,
      );
    }
    const baselinePath = writeBaseline(root, []);
    const started = Date.now();
    const { status } = run(root, src, baselinePath);
    const elapsed = Date.now() - started;
    assert.equal(status, 1); // every generated file is a fresh violation
    assert.ok(elapsed < 1500, `expected scan of 200 files under 1500ms, took ${elapsed}ms`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// -- gate-red reproduction -------------------------------------------------------

test("[gate red] new file with single-sample perf assertion causes exit 1", () => {
  const { root, src } = makeWorkspace();
  try {
    writeFileSync(
      join(src, "NewGateRed.test.tsx"),
      `const t0 = performance.now();\nexpect(performance.now() - t0).toBeLessThan(50);\n`,
    );
    const baselinePath = writeBaseline(root, []);
    const result = run(root, src, baselinePath);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /FAIL.*NewGateRed/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
