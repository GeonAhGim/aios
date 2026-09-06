import { describe, expect, it } from "vitest";

import type { IndicatorCatalogEntry } from "../../plugins/indicatorPlugin";
import { VERIFIED_KERNEL_PINS, isVerifiedIndicator, resolveVerifiedIndicators } from "../verifiedIndicators";

function smaEntry(overrides: Partial<IndicatorCatalogEntry> = {}): IndicatorCatalogEntry {
  return {
    name: "SMA",
    tier: "core",
    category: "overlap",
    version: "ind-v1",
    hash: VERIFIED_KERNEL_PINS.SMA!.entryHash,
    inputs: ["close"],
    outputs: ["value"],
    ...overrides,
  };
}

describe("resolveVerifiedIndicators", () => {
  it("fails closed to an empty whitelist when the catalog is unavailable", () => {
    expect(resolveVerifiedIndicators(null).size).toBe(0);
    expect(resolveVerifiedIndicators(undefined).size).toBe(0);
    expect(resolveVerifiedIndicators([]).size).toBe(0);
  });

  it("includes a pinned indicator whose live tier and entry_hash match", () => {
    const whitelist = resolveVerifiedIndicators([smaEntry()]);
    expect(whitelist.has("SMA")).toBe(true);
    expect(whitelist.get("SMA")?.name).toBe("SMA");
  });

  it("excludes a pinned name whose entry_hash drifted from the server", () => {
    const whitelist = resolveVerifiedIndicators([smaEntry({ hash: "0".repeat(64) })]);
    expect(whitelist.has("SMA")).toBe(false);
  });

  it("excludes a pinned name whose tier moved off CORE", () => {
    const whitelist = resolveVerifiedIndicators([smaEntry({ tier: "oss" })]);
    expect(whitelist.has("SMA")).toBe(false);
  });

  it("ignores catalog entries with no pin at all (not a ported client kernel)", () => {
    const whitelist = resolveVerifiedIndicators([
      { name: "ADX", tier: "core", category: "momentum", version: "ind-v1", hash: "f".repeat(64), inputs: ["high", "low", "close"], outputs: ["value"] },
    ]);
    expect(whitelist.size).toBe(0);
  });

  it("resolves every pinned name independently in one catalog", () => {
    const catalog: IndicatorCatalogEntry[] = Object.entries(VERIFIED_KERNEL_PINS).map(([name, pin]) => ({
      name,
      tier: pin.tier,
      category: "x",
      version: "ind-v1",
      hash: pin.entryHash,
      inputs: ["close"],
      outputs: ["value"],
    }));
    const whitelist = resolveVerifiedIndicators(catalog);
    expect(whitelist.size).toBe(Object.keys(VERIFIED_KERNEL_PINS).length);
  });
});

describe("isVerifiedIndicator", () => {
  it("mirrors resolveVerifiedIndicators for a single name", () => {
    expect(isVerifiedIndicator("SMA", [smaEntry()])).toBe(true);
    expect(isVerifiedIndicator("SMA", [])).toBe(false);
    expect(isVerifiedIndicator("SMA", [smaEntry({ hash: "1".repeat(64) })])).toBe(false);
  });
});
