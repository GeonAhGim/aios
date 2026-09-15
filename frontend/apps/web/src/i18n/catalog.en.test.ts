import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { catalogKo } from "./catalog.ko";
import { catalogEn } from "./catalog.en";
import i18next, { initI18n } from "./index";

// task-2686 (UX-2): catalogEn is a mechanically-drafted mirror of catalogKo (102
// legacy.* namespaces extracted from the baseline files, plus a hand-translated
// common/errors/ai). The one invariant that actually matters here is key parity --
// `satisfies Stringify<CatalogKo>` in catalog.en.ts already catches this at compile
// time, but a *runtime* check catches it too (e.g. if either catalog were ever
// built from untyped JSON) and lets us assert the shape directly in a test
// (ADR-2026-09-09-C D2: negative >=3, failure injection 1, perf assertion 1,
// gate-red repro 1).

function collectLeafPaths(value: unknown, prefix = ""): string[] {
  if (typeof value === "string") return [prefix];
  if (value === null || typeof value !== "object") return [prefix];
  const paths: string[] = [];
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    paths.push(...collectLeafPaths(child, prefix ? `${prefix}.${key}` : key));
  }
  return paths;
}

function diffKeyPaths(a: unknown, b: unknown): { onlyInA: string[]; onlyInB: string[] } {
  const pathsA = new Set(collectLeafPaths(a));
  const pathsB = new Set(collectLeafPaths(b));
  return {
    onlyInA: [...pathsA].filter((p) => !pathsB.has(p)),
    onlyInB: [...pathsB].filter((p) => !pathsA.has(p)),
  };
}

describe("catalogEn / catalogKo key parity", () => {
  it("covers every leaf key path in catalogKo, including the legacy.* namespaces UX-2 extracted", () => {
    const { onlyInA, onlyInB } = diffKeyPaths(catalogKo, catalogEn);
    expect(onlyInA).toEqual([]);
    expect(onlyInB).toEqual([]);
    // Sanity that this is actually the big extraction, not an empty/near-empty catalog.
    expect(collectLeafPaths(catalogKo).length).toBeGreaterThan(500);
  });

  it("[negative] diffKeyPaths detects a key present in ko but missing from en", () => {
    const ko = { common: { save: "저장", cancel: "취소" } };
    const en = { common: { save: "Save" } };
    const { onlyInA, onlyInB } = diffKeyPaths(ko, en);
    expect(onlyInA).toEqual(["common.cancel"]);
    expect(onlyInB).toEqual([]);
  });

  it("[negative] diffKeyPaths detects an extra key present in en but not in ko", () => {
    const ko = { common: { save: "저장" } };
    const en = { common: { save: "Save", cancel: "Cancel" } };
    const { onlyInA, onlyInB } = diffKeyPaths(ko, en);
    expect(onlyInA).toEqual([]);
    expect(onlyInB).toEqual(["common.cancel"]);
  });

  it("[negative] diffKeyPaths flags a leaf-vs-object shape mismatch as a key-path divergence", () => {
    // If a translator accidentally nests a value (`{ save: { label: "Save" } }`)
    // instead of `{ save: "Save" }`, the leaf path changes from "common.save" to
    // "common.save.label" -- this must not silently pass as "same key".
    const ko = { common: { save: "저장" } };
    const enNested = { common: { save: { label: "Save" } } };
    const { onlyInA, onlyInB } = diffKeyPaths(ko, enNested);
    expect(onlyInA).toEqual(["common.save"]);
    expect(onlyInB).toEqual(["common.save.label"]);
  });

  it("[perf budget] diffing the full ko/en catalogs (700+ leaf keys) stays under 100ms", () => {
    const started = performance.now();
    const { onlyInA, onlyInB } = diffKeyPaths(catalogKo, catalogEn);
    const elapsed = performance.now() - started;
    expect(onlyInA).toEqual([]);
    expect(onlyInB).toEqual([]);
    expect(elapsed).toBeLessThan(100);
  });
});

describe("en resource wiring", () => {
  it("resolves a legacy key through the en resource once switched", async () => {
    initI18n();
    await i18next.changeLanguage("en");
    try {
      expect(i18next.t("legacy.candleQualityBadge.t3")).toBe("Quality OK");
    } finally {
      await i18next.changeLanguage("ko");
    }
  });

  it("[failure injection] a key dropped from the en bundle at runtime falls back to ko (fallbackLng) instead of throwing or showing a raw key", async () => {
    // Simulate a translator having dropped a key from the en bundle (e.g. a bad
    // merge). index.ts sets fallbackLng: DEFAULT_LANGUAGE ("ko"), so the user
    // still sees a real translated string, not a broken "legacy.foo.bar" key.
    initI18n();
    i18next.removeResourceBundle("en", "translation");
    i18next.addResourceBundle("en", "translation", { legacy: {} }, true, true);
    await i18next.changeLanguage("en");
    try {
      expect(i18next.t("legacy.candleQualityBadge.t3" as never)).toBe("품질 정상");
    } finally {
      await i18next.changeLanguage("ko");
      // restore the real en bundle for any later test in this file/session
      i18next.addResourceBundle("en", "translation", catalogEn, true, true);
    }
    expect(i18next.t("legacy.candleQualityBadge.t3")).toBe("품질 정상");
  });
});

describe("[gate-red repro] a file UX-2 cleaned up regresses immediately, unlike pre-UX-2 baseline files", () => {
  function makeWorkspace() {
    const root = mkdtempSync(join(tmpdir(), "ux2-i18n-gatered-"));
    const src = join(root, "src");
    mkdirSync(src, { recursive: true });
    return { root, src };
  }

  function run(root: string, src: string, baselinePath: string) {
    try {
      const stdout = execFileSync(
        process.execPath,
        [join(process.cwd(), "..", "..", "scripts", "check_i18n_literals.mjs"), "--root", src, "--base", root, "--baseline", baselinePath],
        { encoding: "utf-8" },
      );
      return { status: 0, stdout };
    } catch (err) {
      const e = err as { status?: number; stdout?: string };
      return { status: e.status ?? 1, stdout: e.stdout ?? "" };
    }
  }

  it("a component with 0 baseline (post-UX-2) fails the instant it regains a hardcoded literal", () => {
    const { root, src } = makeWorkspace();
    try {
      // Mirrors CandleQualityBadge.tsx's converted shape: baseline is 0 (i.e. the
      // file is no longer tracked at all), so re-adding a literal is a fresh
      // violation rather than "grew past N" -- exactly what "리터럴 0건" buys us.
      writeFileSync(
        join(src, "CandleQualityBadge.tsx"),
        `export function Badge() {\n  return <span>품질 정상</span>;\n}\n`,
      );
      const baselinePath = join(root, "baseline.json");
      writeFileSync(baselinePath, JSON.stringify({ files: {} }));
      const { status, stdout } = run(root, src, baselinePath);
      expect(status).toBe(1);
      expect(stdout).toMatch(/FAIL: src\/CandleQualityBadge\.tsx:1 — new hardcoded literal/);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
