// task-2027: concrete-rejection tests for the unwired-module ratchet. Per the
// project's ratchet DoD (see check_frontend_file_size.test.mjs), asserting only the
// passing path is not acceptable — these tests must show the exit code and the
// offending "FAIL: <module>" line for each failure mode.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseIndexExports, findWiredModules, checkRatchet } from "./check_unwired_modules.mjs";

const SCRIPT = join(import.meta.dirname, "check_unwired_modules.mjs");

function makeWorkspace() {
  const root = mkdtempSync(join(tmpdir(), "unwired-modules-"));
  const webSrc = join(root, "web-src");
  mkdirSync(webSrc, { recursive: true });
  const indexPath = join(root, "index.ts");
  return { root, webSrc, indexPath };
}

function writeIndex(indexPath, content) {
  writeFileSync(indexPath, content);
}

function writeBaseline(root, modules) {
  const path = join(root, "baseline.json");
  writeFileSync(path, JSON.stringify({ modules }));
  return path;
}

function run(indexPath, webSrc, baselinePath) {
  try {
    const stdout = execFileSync(
      process.execPath,
      [SCRIPT, "--index", indexPath, "--web-src", webSrc, "--baseline", baselinePath],
      { encoding: "utf-8" },
    );
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status, stdout: err.stdout };
  }
}

test("parseIndexExports maps exported symbols (incl. aliases) to their module path", () => {
  const { modules, symbolToModule } = parseIndexExports(
    'export type { Foo } from "./core/foo";\nexport { createFoo, bar as baz } from "./core/foo";\n',
  );
  assert.deepEqual([...modules], ["core/foo"]);
  assert.equal(symbolToModule.get("Foo"), "core/foo");
  assert.equal(symbolToModule.get("createFoo"), "core/foo");
  assert.equal(symbolToModule.get("baz"), "core/foo");
});

test("findWiredModules resolves both deep submodule imports and barrel imports", () => {
  const symbolToModule = new Map([["createFoo", "core/foo"]]);
  const deep = findWiredModules('import { helper } from "@aios/chart-engine/src/render/helper";', symbolToModule);
  assert.ok(deep.has("render/helper"));
  const barrel = findWiredModules('import { createFoo } from "@aios/chart-engine";', symbolToModule);
  assert.ok(barrel.has("core/foo"));
});

test("checkRatchet: a module not in baseline is a violation; a baseline module no longer unwired is improvable", () => {
  const { violations, improvable } = checkRatchet(["render/lod"], ["core/renderer"]);
  assert.deepEqual(violations, ["render/lod"]);
  assert.deepEqual(improvable, ["core/renderer"]);
});

test("rejects a chart-engine export that no apps/web production file imports and is not baselined", () => {
  const { root, webSrc, indexPath } = makeWorkspace();
  try {
    writeIndex(indexPath, 'export { createOrphan } from "./compute/orphan";\n');
    writeFileSync(join(webSrc, "Page.tsx"), 'export const x = 1;\n');
    const baseline = writeBaseline(root, []);
    const { status, stdout } = run(indexPath, webSrc, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: compute\/orphan/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("passes when the only unwired module is already pinned in the baseline", () => {
  const { root, webSrc, indexPath } = makeWorkspace();
  try {
    writeIndex(indexPath, 'export { createOrphan } from "./compute/orphan";\n');
    writeFileSync(join(webSrc, "Page.tsx"), 'export const x = 1;\n');
    const baseline = writeBaseline(root, ["compute/orphan"]);
    const { status, stdout } = run(indexPath, webSrc, baseline);
    assert.equal(status, 0);
    assert.match(stdout, /OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a deep-path import from a production file counts as wiring", () => {
  const { root, webSrc, indexPath } = makeWorkspace();
  try {
    writeIndex(indexPath, 'export { createFoo } from "./core/foo";\n');
    writeFileSync(join(webSrc, "Page.tsx"), 'import { createFoo } from "@aios/chart-engine/src/core/foo";\n');
    const baseline = writeBaseline(root, []);
    const { status, stdout } = run(indexPath, webSrc, baseline);
    assert.equal(status, 0);
    assert.match(stdout, /OK/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a module only imported from a *.test.tsx file is still reported as unwired", () => {
  const { root, webSrc, indexPath } = makeWorkspace();
  try {
    writeIndex(indexPath, 'export { createFoo } from "./core/foo";\n');
    writeFileSync(join(webSrc, "Page.tsx"), 'export const x = 1;\n');
    writeFileSync(join(webSrc, "Page.test.tsx"), 'import { createFoo } from "@aios/chart-engine/src/core/foo";\n');
    const baseline = writeBaseline(root, []);
    const { status, stdout } = run(indexPath, webSrc, baseline);
    assert.equal(status, 1);
    assert.match(stdout, /FAIL: core\/foo/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a baseline module that got wired is reported as improvable, not a failure", () => {
  const { root, webSrc, indexPath } = makeWorkspace();
  try {
    writeIndex(indexPath, 'export { createFoo } from "./core/foo";\n');
    writeFileSync(join(webSrc, "Page.tsx"), 'import { createFoo } from "@aios/chart-engine/src/core/foo";\n');
    const baseline = writeBaseline(root, ["core/foo"]);
    const { status, stdout } = run(indexPath, webSrc, baseline);
    assert.equal(status, 0);
    assert.match(stdout, /INFO: core\/foo is wired now/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
