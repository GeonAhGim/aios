import { expect } from "vitest";
import type { IndicatorCatalogEntry, IndicatorPluginDefinition, IndicatorStyle } from "../indicatorPlugin";
import { IndicatorPluginError } from "../indicatorPlugin";

export function expectPluginError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(IndicatorPluginError);
    expect((err as IndicatorPluginError).code).toBe(code);
    return;
  }
  throw new Error("expected IndicatorPluginError to be thrown");
}

export const SMA_ENTRY: IndicatorCatalogEntry = {
  name: "SMA",
  tier: "core",
  category: "overlap",
  version: "ind-v1",
  hash: "sma-hash",
  inputs: ["close"],
  outputs: ["value"],
};

export const RSI_ENTRY: IndicatorCatalogEntry = {
  name: "RSI",
  tier: "core",
  category: "momentum",
  version: "ind-v1",
  hash: "rsi-hash",
  inputs: ["close"],
  outputs: ["value"],
};

export const OSS_ENTRY: IndicatorCatalogEntry = {
  name: "COMMUNITY_OSC",
  tier: "oss",
  category: "oss",
  version: "1.0.0",
  hash: "oss-hash",
  inputs: ["close"],
  outputs: ["value"],
};

export function style(overrides: Partial<import("../indicatorPlugin").IndicatorStyleOutput> = {}): IndicatorStyle {
  return { outputs: [{ output: "value", color: "#26a69a", lineWidth: 2, visible: true, ...overrides }] };
}

export function definition(overrides: Partial<IndicatorPluginDefinition> = {}): IndicatorPluginDefinition {
  return {
    instanceId: "sma-1",
    catalogEntry: SMA_ENTRY,
    params: { timeperiod: 20 },
    style: style(),
    ...overrides,
  };
}
