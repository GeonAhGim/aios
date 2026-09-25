import { describe, expect, it } from "vitest";
import { IndicatorPluginError, loadIndicatorCatalog, type IndicatorCatalogPort } from "../indicatorPlugin";
import { PLUGIN_CORRUPTIONS, createRng, pickCorruptedOp } from "./arbitraries";
import { SMA_ENTRY } from "./indicatorPlugin.fixtures";

describe("loadIndicatorCatalog", () => {
  it("returns the catalog page on success", async () => {
    const page = { items: [SMA_ENTRY], nextCursor: null };
    const port: IndicatorCatalogPort = { listIndicators: async () => page };
    const result = await loadIndicatorCatalog(port, { q: "SMA" });
    expect(result).toEqual({ kind: "ok", page });
  });

  it("classifies a thrown failure via routeApiError instead of swallowing it", async () => {
    const port: IndicatorCatalogPort = {
      listIndicators: async () => {
        throw new Error("network down");
      },
    };
    const result = await loadIndicatorCatalog(port);
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.routed.kind).toBe("unknown");
    }
  });
});

describe("failure injection: randomized malformed-operation fuzz", () => {
  // True network/DB failure injection only applies to loadIndicatorCatalog's
  // injected port (the "network down" case above) -- register/unregister/
  // setStyle take an in-memory registry + paneModel, not I/O, so per the
  // DEPTH_CH audit (task-2729) precedent set for the sibling chart-engine
  // pure-domain leaves (1710, 1711), this fuzzer over the malformed-input
  // domain a UI layer (indicator picker, persisted layoutState round-trip)
  // could hand this registry stands in.
  it("fail-closed for 200 seeded corrupted operations (registry/paneModel snapshot untouched after every rejection)", () => {
    const rng = createRng(0x1d17);
    const exercised = new Set<string>();
    for (let i = 0; i < 200; i++) {
      const op = pickCorruptedOp(rng, i);
      exercised.add(op.corruption);
      const registrySnapshot = op.registryBefore;
      const paneModelSnapshot = op.paneModelBefore;
      expect(() => op.apply(), `corruption=${op.corruption} case ${i}`).toThrow(IndicatorPluginError);
      // A thrown registration/style call must never have mutated the
      // snapshot it was applied on top of -- register/unregister/
      // setIndicatorPluginStyle only ever build a *new* object on success.
      expect(op.registryBefore, `corruption=${op.corruption} case ${i}`).toBe(registrySnapshot);
      expect(op.paneModelBefore, `corruption=${op.corruption} case ${i}`).toBe(paneModelSnapshot);
    }
    expect(exercised.size).toBe(PLUGIN_CORRUPTIONS.length);
  });
});
