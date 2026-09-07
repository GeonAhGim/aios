/**
 * CH-19b DoD (4): asserts `layers.ts` does not reimplement vendor's render
 * loop or bypass its canvas/layer API — a static source scan, not a runtime
 * behavior test, because the thing being guarded against ("someone adds a
 * requestAnimationFrame loop that paints directly") would not necessarily
 * show up as a functional test failure until it actually broke something.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SOURCE_PATH = resolve(dirname(fileURLToPath(import.meta.url)), "../layers.ts");
const rawSource = readFileSync(SOURCE_PATH, "utf-8");
// Strip comments before scanning -- the module's own docstring explains, in
// prose, why it deliberately avoids getContext("2d")/transferControlToOffscreen,
// which would otherwise false-positive a naive text match against those names.
const code = rawSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

describe("layers.ts does not reimplement vendor's render loop", () => {
  it("never imports vendor/klinecharts directly (only core/klinecharts.ts may)", () => {
    expect(code).not.toMatch(/from\s+["'][^"']*vendor\/klinecharts/);
  });

  it("schedules no animation/timer loop of its own", () => {
    expect(code).not.toMatch(/requestAnimationFrame|cancelAnimationFrame|setInterval|setTimeout/);
  });

  it("never acquires a 2D context or issues draw calls itself", () => {
    expect(code).not.toMatch(/getContext\(\s*["']2d["']\s*\)/);
    expect(code).not.toMatch(/\.(fillRect|strokeRect|drawImage|beginPath|fillText|stroke|fill)\s*\(/);
  });

  it("reaches vendor panes only through the public chart.getDom() seam", () => {
    expect(code).toMatch(/\.getDom\(/);
  });
});
