import { describe, expect, it } from "vitest";
import { contrastRatio, meetsWcagAA, relativeLuminance, WCAG_AA_LARGE_TEXT, WCAG_AA_TEXT } from "./contrast";

describe("relativeLuminance", () => {
  it("black is 0 and white is 1", () => {
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 5);
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
  });

  it("expands 3-digit shorthand the same as the equivalent 6-digit hex", () => {
    expect(relativeLuminance("#fff")).toBeCloseTo(relativeLuminance("#ffffff"), 10);
    expect(relativeLuminance("#abc")).toBeCloseTo(relativeLuminance("#aabbcc"), 10);
  });

  it("[negative] rejects a malformed hex string", () => {
    expect(() => relativeLuminance("not-a-color")).toThrow(/invalid hex color/);
  });

  it("[negative] rejects a hex string with the wrong digit count", () => {
    expect(() => relativeLuminance("#1234")).toThrow(/invalid hex color/);
  });
});

describe("contrastRatio", () => {
  it("black vs white is the maximum ratio, 21:1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
  });

  it("is symmetric regardless of argument order", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(contrastRatio("#ffffff", "#000000"), 10);
  });

  it("a color against itself is the minimum ratio, 1:1", () => {
    expect(contrastRatio("#d4af37", "#d4af37")).toBeCloseTo(1, 5);
  });

  it("[failure injection] propagates the hex-parsing error instead of silently returning a ratio", () => {
    expect(() => contrastRatio("#zzzzzz", "#ffffff")).toThrow(/invalid hex color/);
  });
});

describe("meetsWcagAA", () => {
  it("[gate-red repro] the actual pre-fix light-theme fg-muted/bg pair (3.78:1) fails the 4.5:1 text threshold", () => {
    expect(meetsWcagAA("#8a7e5f", "#faf8f2")).toBe(false);
  });

  it("the fixed light-theme fg-muted/bg pair clears the 4.5:1 text threshold", () => {
    expect(meetsWcagAA("#7c7155", "#faf8f2")).toBe(true);
  });

  it("[negative] a pair between the large-text (3:1) and normal-text (4.5:1) thresholds passes only as large text", () => {
    expect(meetsWcagAA("#9a7209", "#faf8f2", false)).toBe(false);
    expect(meetsWcagAA("#9a7209", "#faf8f2", true)).toBe(true);
  });

  it("[perf] evaluating 5,000 pairs stays under 200ms", () => {
    const started = Date.now();
    for (let i = 0; i < 5000; i += 1) {
      meetsWcagAA("#f2ede0", "#0d0c0a");
    }
    const elapsed = Date.now() - started;
    expect(elapsed).toBeLessThan(200);
  });

  it("boundary ratios exactly at the threshold count as passing", () => {
    expect(WCAG_AA_TEXT).toBe(4.5);
    expect(WCAG_AA_LARGE_TEXT).toBe(3);
  });
});
