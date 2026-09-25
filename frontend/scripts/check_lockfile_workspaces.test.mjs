// task-3073 (DEEPEN of task-1509/CH-1a): task-1509 fixed frontend/package-lock.json
// missing the @aios/chart-engine workspace entry with a hand-written +11/-0 diff and
// zero tests (docs/audit/DEPTH_CH.md graded it D0 — "테스트 없음, D2 4요건 전부 없음").
// This file supplies the missing evidence: >=3 negative-path assertions, structural
// failure injection, a numeric performance assertion, and — the single item every one
// of the 44 audited CH leaves lacked — an automated before/after gate-red repro that
// reconstructs the exact pre-fix lockfile state and shows it fails the way `npm ci
// --workspaces --include-workspace-root` actually failed
// (escalations/esc-ci-de7c682bb469.json: "Missing: @aios/chart-engine@0.0.0 from lock
// file"), then shows the current committed lockfile passes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  discoverWorkspaceDirs,
  readWorkspacePackage,
  checkLockfileWorkspaces,
} from "./check_lockfile_workspaces.mjs";

const SCRIPT = join(import.meta.dirname, "check_lockfile_workspaces.mjs");
const FRONTEND_ROOT = resolveFrontendRoot();

function resolveFrontendRoot() {
  // this file lives at frontend/scripts/check_lockfile_workspaces.test.mjs
  return join(import.meta.dirname, "..");
}

function makeWorkspaceRoot(dirs) {
  const root = mkdtempSync(join(tmpdir(), "lockfile-workspaces-"));
  for (const glob of ["apps", "packages"]) mkdirSync(join(root, glob), { recursive: true });
  for (const { dir, name, version } of dirs) {
    mkdirSync(join(root, dir), { recursive: true });
    writeFileSync(join(root, dir, "package.json"), JSON.stringify({ name, version }));
  }
  return root;
}

function writeRootFiles(root, { workspaces, lockfile }) {
  writeFileSync(join(root, "package.json"), JSON.stringify({ name: "root", workspaces }));
  writeFileSync(join(root, "package-lock.json"), JSON.stringify(lockfile));
}

function runCli(root) {
  try {
    const stdout = execFileSync(process.execPath, [SCRIPT, "--root", root], { encoding: "utf-8" });
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout };
  }
}

function inSyncLockfile() {
  return {
    packages: {
      "": { name: "root", workspaces: ["packages/*"] },
      "packages/foo": { name: "@aios/foo", version: "0.0.0" },
      "node_modules/@aios/foo": { resolved: "packages/foo", link: true },
    },
  };
}

// --- discoverWorkspaceDirs ---------------------------------------------------

test("discoverWorkspaceDirs expands '<segment>/*' globs to existing package directories", () => {
  const root = makeWorkspaceRoot([{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }]);
  try {
    assert.deepEqual(discoverWorkspaceDirs(root, ["apps/*", "packages/*"]), ["packages/foo"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverWorkspaceDirs negative: a glob segment with no directory yields no entries (not a crash)", () => {
  const root = mkdtempSync(join(tmpdir(), "lockfile-workspaces-empty-"));
  try {
    assert.deepEqual(discoverWorkspaceDirs(root, ["apps/*", "packages/*"]), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverWorkspaceDirs negative: unsupported glob shape throws instead of silently matching nothing", () => {
  assert.throws(() => discoverWorkspaceDirs("/irrelevant", ["packages/**"]), /unsupported workspace glob/);
});

// --- checkLockfileWorkspaces: negative paths ---------------------------------

test("checkLockfileWorkspaces: in-sync lockfile has zero violations (happy path baseline)", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }];
  assert.deepEqual(checkLockfileWorkspaces(pkgs, inSyncLockfile()), []);
});

test("checkLockfileWorkspaces negative: missing packages/<dir> entry reproduces npm ci's own wording", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }];
  const lockfile = { packages: {} };
  const violations = checkLockfileWorkspaces(pkgs, lockfile);
  assert.ok(violations.some((v) => v === "Missing: @aios/foo@0.0.0 from lock file (no packages[\"packages/foo\"] entry)"));
});

test("checkLockfileWorkspaces negative: version drift between package.json and lock entry is flagged", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.1.0" }];
  const lockfile = inSyncLockfile();
  const violations = checkLockfileWorkspaces(pkgs, lockfile);
  assert.ok(violations.some((v) => v.includes("does not satisfy package.json's @aios/foo@0.1.0")));
});

test("checkLockfileWorkspaces negative: missing node_modules link entry is flagged distinctly from a missing package entry", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }];
  const lockfile = { packages: { "packages/foo": { name: "@aios/foo", version: "0.0.0" } } };
  const violations = checkLockfileWorkspaces(pkgs, lockfile);
  assert.ok(violations.some((v) => v.includes('no packages["node_modules/@aios/foo"] link entry')));
});

test("checkLockfileWorkspaces negative: link entry resolving to the wrong directory is flagged", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }];
  const lockfile = {
    packages: {
      "packages/foo": { name: "@aios/foo", version: "0.0.0" },
      "node_modules/@aios/foo": { resolved: "packages/stale-foo", link: true },
    },
  };
  const violations = checkLockfileWorkspaces(pkgs, lockfile);
  assert.ok(violations.some((v) => v.includes('resolved is "packages/stale-foo"')));
});

// --- failure injection: structurally corrupt a previously-valid lockfile ----

test("failure injection: corrupting a valid lockfile clone (link demoted from symlink) is caught, not silently accepted", () => {
  const pkgs = [{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }];
  const good = inSyncLockfile();
  // simulate a stale/partial `npm install` writing the link entry without "link": true
  const corrupted = structuredClone(good);
  delete corrupted.packages["node_modules/@aios/foo"].link;
  assert.deepEqual(checkLockfileWorkspaces(pkgs, good), []);
  const violations = checkLockfileWorkspaces(pkgs, corrupted);
  assert.ok(violations.some((v) => v.includes('missing "link": true')));
});

test("failure injection: wiping a workspace's lock entries out of an otherwise-valid multi-package lockfile only breaks that one package", () => {
  const pkgs = [
    { dir: "packages/foo", name: "@aios/foo", version: "0.0.0" },
    { dir: "packages/bar", name: "@aios/bar", version: "0.0.0" },
  ];
  const good = {
    packages: {
      "": { name: "root", workspaces: ["packages/*"] },
      "packages/foo": { name: "@aios/foo", version: "0.0.0" },
      "node_modules/@aios/foo": { resolved: "packages/foo", link: true },
      "packages/bar": { name: "@aios/bar", version: "0.0.0" },
      "node_modules/@aios/bar": { resolved: "packages/bar", link: true },
    },
  };
  const corrupted = structuredClone(good);
  delete corrupted.packages["packages/bar"];
  delete corrupted.packages["node_modules/@aios/bar"];
  const violations = checkLockfileWorkspaces(pkgs, corrupted);
  assert.equal(violations.length, 1);
  assert.match(violations[0], /@aios\/bar/);
});

// --- numeric performance assertion -------------------------------------------

test("performance: checking the real frontend/ lockfile against its real workspaces completes well under 500ms", () => {
  const rootPkg = JSON.parse(readFileSync(join(FRONTEND_ROOT, "package.json"), "utf-8"));
  const lockfile = JSON.parse(readFileSync(join(FRONTEND_ROOT, "package-lock.json"), "utf-8"));
  const dirs = discoverWorkspaceDirs(FRONTEND_ROOT, rootPkg.workspaces);
  assert.ok(dirs.length >= 4, "expected apps/web + packages/* to be discovered");

  const start = performance.now();
  const pkgs = dirs.map((dir) => readWorkspacePackage(FRONTEND_ROOT, dir));
  checkLockfileWorkspaces(pkgs, lockfile);
  const elapsedMs = performance.now() - start;

  assert.ok(elapsedMs < 500, `expected lockfile-workspace check to run under 500ms, took ${elapsedMs}ms`);
});

// --- gate-red repro: reproduce the exact task-1509 CI break, then prove the fix ----

test("gate-red repro: removing @aios/chart-engine from a clone of the real lockfile reproduces the exact npm ci failure task-1509 fixed", () => {
  const rootPkg = JSON.parse(readFileSync(join(FRONTEND_ROOT, "package.json"), "utf-8"));
  const realLockfile = JSON.parse(readFileSync(join(FRONTEND_ROOT, "package-lock.json"), "utf-8"));
  const dirs = discoverWorkspaceDirs(FRONTEND_ROOT, rootPkg.workspaces);
  const pkgs = dirs.map((dir) => readWorkspacePackage(FRONTEND_ROOT, dir));

  // RED: reconstruct the pre-e095abc state (CH-1a's 87da8d1 added the workspace dir
  // but never regenerated the lockfile) by deleting exactly the two entries
  // e095abc's +11/-0 diff added.
  const brokenLockfile = structuredClone(realLockfile);
  delete brokenLockfile.packages["packages/chart-engine"];
  delete brokenLockfile.packages["node_modules/@aios/chart-engine"];
  const redViolations = checkLockfileWorkspaces(pkgs, brokenLockfile);
  assert.ok(
    redViolations.some((v) => v === "Missing: @aios/chart-engine@0.0.0 from lock file (no packages[\"packages/chart-engine\"] entry)"),
    `expected the reconstructed pre-fix lockfile to reproduce the chart-engine CI break, got: ${JSON.stringify(redViolations)}`,
  );

  // GREEN: the actual committed lockfile (post e095abc + this leaf) is in sync.
  const greenViolations = checkLockfileWorkspaces(pkgs, realLockfile);
  assert.deepEqual(greenViolations, []);
});

test("gate-red repro (CLI/process level): the checker binary exits 1 with FAIL on a broken fixture and 0 with OK once fixed", () => {
  const root = makeWorkspaceRoot([{ dir: "packages/foo", name: "@aios/foo", version: "0.0.0" }]);
  try {
    writeRootFiles(root, { workspaces: ["packages/*"], lockfile: { packages: {} } });
    const red = runCli(root);
    assert.equal(red.status, 1);
    assert.match(red.stdout, /FAIL: Missing: @aios\/foo@0\.0\.0 from lock file/);
    assert.match(red.stdout, /1 violation/);

    writeRootFiles(root, { workspaces: ["packages/*"], lockfile: inSyncLockfile() });
    const green = runCli(root);
    assert.equal(green.status, 0);
    assert.match(green.stdout, /lockfile-workspaces: OK \(1 workspace package\(s\) in sync\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
