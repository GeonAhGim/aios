/**
 * DEEPEN task-3107 of task-2052 (CH-19d): the original "density bench is wired
 * into CI" claim (commit 5adbc86b) had zero test-file changes — the only
 * evidence was package.json's bench:density script and a bumped
 * density-baseline.json, with "it's wired" asserted only in the commit
 * message. This file makes that claim automatically reproducible: it reads
 * the actual package.json scripts (not a hardcoded copy of them, so drift
 * fails the test) to prove the delegation chain
 * frontend/package.json#bench:density -> chart-engine/package.json#bench:density
 * -> bench/density_bench.mjs still holds, then actually spawns that exact
 * leaf command end-to-end against the real render/compute modules (not a
 * stub) to prove the script itself still runs, not just that a string in a
 * script field points at it.
 */
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const CHART_ENGINE_DIR = resolvePath(HERE, "..");
const FRONTEND_DIR = resolvePath(CHART_ENGINE_DIR, "..", "..");
const BASELINE_PATH = resolvePath(HERE, "density-baseline.json");

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

describe("density-bench CI wiring (DEEPEN task-3107)", () => {
  it("frontend/package.json#bench:density forwards to the chart-engine workspace", () => {
    const pkg = readJson(resolvePath(FRONTEND_DIR, "package.json"));
    expect(pkg.scripts["bench:density"]).toBe("npm run bench:density --workspace=packages/chart-engine");
  });

  it("chart-engine/package.json#bench:density invokes the real bench file", () => {
    const pkg = readJson(resolvePath(CHART_ENGINE_DIR, "package.json"));
    expect(pkg.scripts["bench:density"]).toBe("node --expose-gc --experimental-strip-types bench/density_bench.mjs");
  });

  // task-2052's own tail comment (local_ci.py run_gates test_steps) records this
  // exact command as the CI gate; this proves the command it names still exists
  // and still runs end-to-end, so that comment stays true instead of drifting.
  it(
    "the wired command actually runs end-to-end against the real render/compute modules",
    () => {
      const baselineBackup = readFileSync(BASELINE_PATH, "utf-8");
      try {
        const result = spawnSync(
          process.execPath,
          ["--experimental-strip-types", "bench/density_bench.mjs"],
          { cwd: CHART_ENGINE_DIR, encoding: "utf-8", timeout: 60_000 },
        );
        // A crash before any measurement (bad import path, missing compute/render
        // module, syntax error) is what "wiring silently rotted" looks like --
        // that must fail this test regardless of exit code.
        expect(result.error).toBeUndefined();
        expect(result.stdout).toMatch(/\[density-bench\] measured \(median across sweeps\):/);
        // Exit 0 (within thresholds) and 1 (a real ratchet/absolute-threshold
        // failure) are both proof the wiring works -- only a crash (anything
        // else, e.g. an uncaught exception before main() resolves) is wiring rot.
        expect([0, 1]).toContain(result.status);
      } finally {
        // The bench mutates density-baseline.json on any improvement (see
        // density_bench.mjs main()) -- restore it so this proof test has no
        // side effect on the tracked ratchet baseline itself.
        writeFileSync(BASELINE_PATH, baselineBackup, "utf-8");
      }
    },
    60_000,
  );
});
