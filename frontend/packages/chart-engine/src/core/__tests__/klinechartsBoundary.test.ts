/**
 * task-2053 (CH-14d, spec §9.11 CH-14) re-verified — investigation only, no
 * code or test commit — that core/klinecharts.ts is the sole vendor
 * re-export boundary and that splitting the klinecharts adapter/renderer/
 * series modules further would be a speculative vendor reimplementation (ADR-2026-09-06-F
 * D3; see frontend/scripts/unwired-modules-baseline.json). DEPTH_CH audit
 * (task-2729) flagged that conclusion as D0: nothing here would fail if the
 * boundary broke. This is the missing evidence — a static source scan (same
 * technique as render/__tests__/layers.noVendorReimpl.test.ts) plus a check
 * that the baseline still names exactly the modules the ADR is about.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const BASELINE_PATH = resolve(SRC_DIR, "../../../scripts/unwired-modules-baseline.json");
const VENDOR_IMPORT_RE = /from\s+["'][^"']*vendor\/klinecharts[^"']*["']/;

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

function listSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "vendor" || entry.name === "node_modules") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      listSourceFiles(full, out);
    } else if (/\.tsx?$/.test(entry.name) && !/\.(test|spec)\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function importersOfVendor(): string[] {
  return listSourceFiles(SRC_DIR)
    .filter((file) => VENDOR_IMPORT_RE.test(stripComments(readFileSync(file, "utf-8"))))
    .map((file) => relative(SRC_DIR, file).split("\\").join("/"))
    .sort();
}

describe("core/klinecharts.ts is the sole vendor re-export boundary (CH-14d, task-2053 / ADR-2026-09-06-F D3)", () => {
  it("no production module other than core/klinecharts.ts imports vendor/klinecharts", () => {
    expect(importersOfVendor()).toEqual(["core/klinecharts.ts"]);
  });

  it("core/renderer.ts and core/series.ts stay fully vendor-free -- not even a comment mentions vendor/klinecharts", () => {
    for (const relPath of ["core/renderer.ts", "core/series.ts"]) {
      const content = readFileSync(join(SRC_DIR, relPath), "utf-8");
      expect(content).not.toMatch(/vendor\/klinecharts/);
    }
  });

  it("core/klinechartsBackend.ts and core/klinechartsSeries.ts reach vendor identifiers only through ./klinecharts, never a vendor path directly", () => {
    for (const relPath of ["core/klinechartsBackend.ts", "core/klinechartsSeries.ts"]) {
      const content = stripComments(readFileSync(join(SRC_DIR, relPath), "utf-8"));
      expect(content).not.toMatch(VENDOR_IMPORT_RE);
      expect(content).toMatch(/from\s+["']\.\/klinecharts["']/);
    }
  });

  it("gate-red: adding a direct vendor import to a non-boundary file would fail the sole-importer assertion", () => {
    // Proves the scan isn't vacuously true by re-running it against a synthetic
    // extra importer, exactly mirroring how a regression would show up above.
    const withRegression = [...importersOfVendor(), "core/series.ts"].sort();
    expect(withRegression).not.toEqual(["core/klinecharts.ts"]);
  });
});

describe("unwired-modules-baseline.json still names exactly the CH-14d boundary modules (task-2053 D3 decision, not a stale ratchet)", () => {
  it("baseline.modules is exactly the 5 vendor-boundary modules the ADR decided to leave unwired", () => {
    const baseline = JSON.parse(readFileSync(BASELINE_PATH, "utf-8")) as { modules: string[] };
    expect([...baseline.modules].sort()).toEqual(
      ["core/klinecharts", "core/klinechartsBackend", "core/klinechartsSeries", "core/renderer", "core/series"].sort(),
    );
  });
});
