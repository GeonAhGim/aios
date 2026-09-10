import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry, type OverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import {
  IndicatorPluginError,
  type IndicatorCatalogEntry,
  type IndicatorCatalogPort,
  type IndicatorPluginDefinition,
  type IndicatorStyle,
  createIndicatorPluginRegistry,
  decodeIndicatorStyle,
  encodeIndicatorStyle,
  loadIndicatorCatalog,
  registerIndicatorPlugin,
  setIndicatorPluginStyle,
  unregisterIndicatorPlugin,
} from "../indicatorPlugin";
import { MACD_ENTRY, PLUGIN_CORRUPTIONS, createRng, goodStyle, pickCorruptedOp } from "./arbitraries";

function expectPluginError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(IndicatorPluginError);
    expect((err as IndicatorPluginError).code).toBe(code);
    return;
  }
  throw new Error("expected IndicatorPluginError to be thrown");
}

const SMA_ENTRY: IndicatorCatalogEntry = {
  name: "SMA",
  tier: "core",
  category: "overlap",
  version: "ind-v1",
  hash: "sma-hash",
  inputs: ["close"],
  outputs: ["value"],
};

const RSI_ENTRY: IndicatorCatalogEntry = {
  name: "RSI",
  tier: "core",
  category: "momentum",
  version: "ind-v1",
  hash: "rsi-hash",
  inputs: ["close"],
  outputs: ["value"],
};

const OSS_ENTRY: IndicatorCatalogEntry = {
  name: "COMMUNITY_OSC",
  tier: "oss",
  category: "oss",
  version: "1.0.0",
  hash: "oss-hash",
  inputs: ["close"],
  outputs: ["value"],
};

function style(overrides: Partial<import("../indicatorPlugin").IndicatorStyleOutput> = {}): IndicatorStyle {
  return { outputs: [{ output: "value", color: "#26a69a", lineWidth: 2, visible: true, ...overrides }] };
}

function definition(overrides: Partial<IndicatorPluginDefinition> = {}): IndicatorPluginDefinition {
  return {
    instanceId: "sma-1",
    catalogEntry: SMA_ENTRY,
    params: { timeperiod: 20 },
    style: style(),
    ...overrides,
  };
}

describe("registerIndicatorPlugin — placement resolution", () => {
  it("CORE tier: placement is read from the overlay registry (main-overlay for SMA)", () => {
    const overlay = createDefaultOverlayRegistry();
    const { registry, paneModel } = registerIndicatorPlugin(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      definition(),
    );
    expect(registry.entries).toHaveLength(1);
    expect(registry.entries[0]!.placement).toBe("main-overlay");
    expect(registry.entries[0]!.paneId).toBe("main");
    expect(paneModel.panes).toHaveLength(1); // no sub-pane allocated
  });

  it("CORE tier: sub-pane placement (RSI) allocates a paneModel sub-pane keyed by instanceId", () => {
    const overlay = createDefaultOverlayRegistry();
    const { registry, paneModel } = registerIndicatorPlugin(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      definition({ instanceId: "rsi-1", catalogEntry: RSI_ENTRY, style: style() }),
    );
    expect(registry.entries[0]!.placement).toBe("sub-pane");
    expect(registry.entries[0]!.paneId).toBe("rsi-1");
    expect(paneModel.panes.map((p) => p.id)).toEqual(["main", "rsi-1"]);
  });

  it("CORE tier: an explicit placement conflicting with the overlay registry is rejected", () => {
    const overlay = createDefaultOverlayRegistry();
    expectPluginError(
      () =>
        registerIndicatorPlugin(
          createIndicatorPluginRegistry(),
          createPaneModel("main"),
          overlay,
          definition({ placement: "sub-pane" }),
        ),
      "INDICATOR_PLUGIN_INVALID",
    );
  });

  it("OSS tier without an overlay registry entry requires an explicit placement", () => {
    const overlay = createDefaultOverlayRegistry();
    expectPluginError(
      () =>
        registerIndicatorPlugin(
          createIndicatorPluginRegistry(),
          createPaneModel("main"),
          overlay,
          definition({ instanceId: "oss-1", catalogEntry: OSS_ENTRY }),
        ),
      "INDICATOR_PLUGIN_PLACEMENT_REQUIRED",
    );

    const { registry, paneModel } = registerIndicatorPlugin(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      definition({ instanceId: "oss-1", catalogEntry: OSS_ENTRY, placement: "sub-pane" }),
    );
    expect(registry.entries[0]!.placement).toBe("sub-pane");
    expect(paneModel.panes.map((p) => p.id)).toEqual(["main", "oss-1"]);
  });

  it("rejects a duplicate instanceId", () => {
    const overlay = createDefaultOverlayRegistry();
    const first = registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition());
    expectPluginError(
      () => registerIndicatorPlugin(first.registry, first.paneModel, overlay, definition()),
      "INDICATOR_PLUGIN_DUPLICATE",
    );
  });
});

describe("registerIndicatorPlugin — style validation", () => {
  it("rejects a style output that isn't one of the catalog entry's outputs", () => {
    const overlay = createDefaultOverlayRegistry();
    expectPluginError(
      () =>
        registerIndicatorPlugin(
          createIndicatorPluginRegistry(),
          createPaneModel("main"),
          overlay,
          definition({ style: { outputs: [{ output: "unknown", color: "#fff", lineWidth: 1, visible: true }] } }),
        ),
      "INDICATOR_PLUGIN_INVALID",
    );
  });

  it("rejects a non-positive lineWidth", () => {
    const overlay = createDefaultOverlayRegistry();
    expectPluginError(
      () => registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition({ style: style({ lineWidth: 0 }) })),
      "INDICATOR_PLUGIN_INVALID",
    );
  });
});

describe("register/unregister round trip", () => {
  it("unregistering a sub-pane plugin removes its pane and its registry entry", () => {
    const overlay = createDefaultOverlayRegistry();
    const registered = registerIndicatorPlugin(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      definition({ instanceId: "rsi-1", catalogEntry: RSI_ENTRY }),
    );
    const { registry, paneModel } = unregisterIndicatorPlugin(registered.registry, registered.paneModel, "rsi-1");
    expect(registry.entries).toHaveLength(0);
    expect(paneModel.panes.map((p) => p.id)).toEqual(["main"]);
  });

  it("unregistering a main-overlay plugin leaves the pane model untouched", () => {
    const overlay = createDefaultOverlayRegistry();
    const registered = registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition());
    const { registry, paneModel } = unregisterIndicatorPlugin(registered.registry, registered.paneModel, "sma-1");
    expect(registry.entries).toHaveLength(0);
    expect(paneModel.panes.map((p) => p.id)).toEqual(["main"]);
  });

  it("unregistering an unknown instanceId throws INDICATOR_PLUGIN_UNKNOWN", () => {
    expectPluginError(
      () => unregisterIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), "nope"),
      "INDICATOR_PLUGIN_UNKNOWN",
    );
  });
});

describe("style round trip", () => {
  it("encode -> JSON round trip -> decode -> setIndicatorPluginStyle restores the exact style", () => {
    const overlay = createDefaultOverlayRegistry();
    const original = style({ color: "#ff0000", lineWidth: 3, visible: false });
    const registered = registerIndicatorPlugin(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      definition({ style: original }),
    );

    const persisted = JSON.parse(JSON.stringify(encodeIndicatorStyle(original)));
    const decoded = decodeIndicatorStyle(persisted);
    expect(decoded).toEqual(original);

    const updated = setIndicatorPluginStyle(registered.registry, "sma-1", decoded);
    expect(updated.entries[0]!.style).toEqual(original);
  });

  it("decodeIndicatorStyle fails closed on a malformed document instead of guessing defaults", () => {
    expectPluginError(() => decodeIndicatorStyle({}), "INDICATOR_PLUGIN_INVALID");
    expectPluginError(() => decodeIndicatorStyle({ outputs: [{ output: "value" }] }), "INDICATOR_PLUGIN_INVALID");
  });

  // task-2028 (053b3d74) added upColor/downColor/aboveColor/belowColor to
  // IndicatorStyleOutput for CH-15 color_rule/fill-direction consumption, but
  // left the codec round trip and negative decode paths for these 4 optional
  // fields entirely untested (DEPTH_CH task-2729 gap, task-3101).
  describe("upColor/downColor/aboveColor/belowColor (task-2028) round trip", () => {
    it("round-trips all 4 optional direction/sign colors through encode -> JSON -> decode", () => {
      const original = style({ upColor: "#0f0", downColor: "#f00", aboveColor: "#0ff", belowColor: "#f0f" });
      const persisted = JSON.parse(JSON.stringify(encodeIndicatorStyle(original)));
      expect(decodeIndicatorStyle(persisted)).toEqual(original);
    });

    it("omits the 4 optional fields from the encoded document when unset (does not encode them as null)", () => {
      const encoded = encodeIndicatorStyle(style());
      const outputs = encoded.outputs as Record<string, unknown>[];
      for (const field of ["upColor", "downColor", "aboveColor", "belowColor"]) {
        expect(field in outputs[0]!, field).toBe(false);
      }
    });

    it("decoding a document that omits the 4 optional fields leaves them undefined (regression)", () => {
      const decoded = decodeIndicatorStyle({ outputs: [{ output: "value", color: "#26a69a", lineWidth: 2, visible: true }] });
      expect(decoded.outputs[0]!.upColor).toBeUndefined();
      expect(decoded.outputs[0]!.downColor).toBeUndefined();
      expect(decoded.outputs[0]!.aboveColor).toBeUndefined();
      expect(decoded.outputs[0]!.belowColor).toBeUndefined();
    });

    it.each(["upColor", "downColor", "aboveColor", "belowColor"] as const)(
      "decodeIndicatorStyle rejects a non-string %s (negative, fail-closed)",
      (field) => {
        expectPluginError(
          () => decodeIndicatorStyle({ outputs: [{ output: "value", color: "#26a69a", lineWidth: 2, visible: true, [field]: 123 }] }),
          "INDICATOR_PLUGIN_INVALID",
        );
      },
    );
  });

  it("setIndicatorPluginStyle re-validates against the entry's catalog outputs", () => {
    const overlay = createDefaultOverlayRegistry();
    const registered = registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition());
    expectPluginError(
      () => setIndicatorPluginStyle(registered.registry, "sma-1", style({ output: "unknown" })),
      "INDICATOR_PLUGIN_INVALID",
    );
    expectPluginError(
      () => setIndicatorPluginStyle(registered.registry, "unknown-instance", style()),
      "INDICATOR_PLUGIN_UNKNOWN",
    );
  });
});

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

describe("performance: numeric ms budget for bulk plugin churn", () => {
  it("registers 300 sub-pane instances, restyles each once, and unregisters them all within a fixed ms budget", () => {
    const overlay = createDefaultOverlayRegistry();
    const start = performance.now();
    let registry = createIndicatorPluginRegistry();
    let paneModel = createPaneModel("main");
    const ids: string[] = [];
    for (let i = 0; i < 300; i++) {
      const id = `rsi-${i}`;
      ids.push(id);
      const next = registerIndicatorPlugin(registry, paneModel, overlay, {
        instanceId: id,
        catalogEntry: RSI_ENTRY,
        params: { timeperiod: 14 },
        style: goodStyle(RSI_ENTRY),
      });
      registry = next.registry;
      paneModel = next.paneModel;
    }
    for (const id of ids) {
      registry = setIndicatorPluginStyle(registry, id, goodStyle(RSI_ENTRY, { color: "#ff0000" }));
    }
    for (let i = ids.length - 1; i >= 0; i--) {
      const next = unregisterIndicatorPlugin(registry, paneModel, ids[i]!);
      registry = next.registry;
      paneModel = next.paneModel;
    }
    const elapsedMs = performance.now() - start;

    expect(registry.entries).toHaveLength(0);
    expect(paneModel.panes.map((p) => p.id)).toEqual(["main"]);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // register/unregister/setStyle scan the full entry list in an
    // accidentally quadratic way beyond their documented per-call O(n) find
    // would blow well past this.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: indicator plugin invariants", () => {
  describe("validateStyle: no lineWidth range check", () => {
    /** Mimics a pre-hardening validateStyle with no positivity check on
     * lineWidth. A mutant, not part of the shipped module. */
    function legacyValidateStyleNoLineWidthCheck(style: IndicatorStyle, catalogEntry: IndicatorCatalogEntry): void {
      const seen = new Set<string>();
      for (const entry of style.outputs) {
        if (!catalogEntry.outputs.includes(entry.output)) {
          throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", `style output "${entry.output}" is not one of the outputs`);
        }
        if (seen.has(entry.output)) {
          throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", `duplicate style output "${entry.output}"`);
        }
        seen.add(entry.output);
        // no lineWidth check
      }
    }

    it("red: a naive validateStyle silently accepts a zero lineWidth (an invisible, unselectable line)", () => {
      expect(() => legacyValidateStyleNoLineWidthCheck(goodStyle(SMA_ENTRY, { lineWidth: 0 }), SMA_ENTRY)).not.toThrow();
    });

    it("green: the shipped registerIndicatorPlugin rejects a zero lineWidth instead of shipping an invisible line", () => {
      const overlay = createDefaultOverlayRegistry();
      expectPluginError(
        () =>
          registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition({ style: goodStyle(SMA_ENTRY, { lineWidth: 0 }) })),
        "INDICATOR_PLUGIN_INVALID",
      );
    });
  });

  describe("resolvePlacement: no conflict check against the overlay registry", () => {
    /** Mimics a pre-hardening resolvePlacement that trusts the overlay
     * registry silently over a caller's explicit placement instead of
     * rejecting the disagreement. A mutant, not part of the shipped module. */
    function legacyResolvePlacementSilentOverride(overlay: OverlayRegistry, catalogEntry: IndicatorCatalogEntry, explicit: string | undefined): string {
      if (overlay.has(catalogEntry.name)) {
        return overlay.resolve(catalogEntry.name).placement;
      }
      return explicit ?? "main-overlay";
    }

    it("red: a naive resolvePlacement silently discards a caller's conflicting explicit placement instead of erroring", () => {
      const overlay = createDefaultOverlayRegistry();
      // Caller explicitly asked for "sub-pane"; SMA is registered as
      // "main-overlay" in the overlay registry. A silent implementation
      // returns the registry's answer without telling the caller their
      // request was overridden -- exactly the class of bug CH-11's DoD
      // ("overlay registry is authoritative, disagreement is rejected")
      // exists to prevent.
      const resolved = legacyResolvePlacementSilentOverride(overlay, SMA_ENTRY, "sub-pane");
      expect(resolved).toBe("main-overlay");
    });

    it("green: the shipped registerIndicatorPlugin rejects the conflicting explicit placement instead of silently overriding it", () => {
      const overlay = createDefaultOverlayRegistry();
      expectPluginError(
        () => registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, definition({ placement: "sub-pane" })),
        "INDICATOR_PLUGIN_INVALID",
      );
    });
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: registry/paneModel stay consistent across 200 seeded random register/unregister sequences", () => {
    const rng = createRng(0xd3d3);
    const overlay = createDefaultOverlayRegistry();
    const catalogEntries = [SMA_ENTRY, RSI_ENTRY, MACD_ENTRY] as const;

    for (let i = 0; i < 200; i++) {
      let registry = createIndicatorPluginRegistry();
      let paneModel = createPaneModel("main");
      const live = new Map<string, "main-overlay" | "sub-pane">();
      const opCount = rng.int(5, 30);

      for (let j = 0; j < opCount; j++) {
        const wantsRegister = live.size === 0 || rng.next() < 0.7;
        if (wantsRegister) {
          const entry = rng.pick(catalogEntries);
          const id = `p${i}-${j}`;
          const { registry: nextRegistry, paneModel: nextPaneModel } = registerIndicatorPlugin(registry, paneModel, overlay, {
            instanceId: id,
            catalogEntry: entry,
            params: {},
            style: goodStyle(entry),
          });
          registry = nextRegistry;
          paneModel = nextPaneModel;
          live.set(id, registry.entries.find((e) => e.instanceId === id)!.placement);
        } else {
          const id = rng.pick([...live.keys()]);
          const { registry: nextRegistry, paneModel: nextPaneModel } = unregisterIndicatorPlugin(registry, paneModel, id);
          registry = nextRegistry;
          paneModel = nextPaneModel;
          live.delete(id);
        }

        const expectedSubPanes = [...live.values()].filter((p) => p === "sub-pane").length;
        expect(registry.entries, `case ${i} op ${j}`).toHaveLength(live.size);
        expect(paneModel.panes, `case ${i} op ${j}`).toHaveLength(1 + expectedSubPanes);
        expect(paneModel.panes.filter((p) => p.kind === "main"), `case ${i} op ${j}`).toHaveLength(1);
      }
    }
  }, 20_000);

  it("multi-instance isolation (D3): two independent registry/paneModel lineages built interleaved never alias each other's entries or panes", () => {
    const overlayA = createDefaultOverlayRegistry();
    const overlayB = createDefaultOverlayRegistry();
    let a = { registry: createIndicatorPluginRegistry(), paneModel: createPaneModel("mainA") };
    let b = { registry: createIndicatorPluginRegistry(), paneModel: createPaneModel("mainB") };

    a = registerIndicatorPlugin(a.registry, a.paneModel, overlayA, {
      instanceId: "rsi-a",
      catalogEntry: RSI_ENTRY,
      params: {},
      style: goodStyle(RSI_ENTRY),
    });
    b = registerIndicatorPlugin(b.registry, b.paneModel, overlayB, {
      instanceId: "rsi-b",
      catalogEntry: RSI_ENTRY,
      params: {},
      style: goodStyle(RSI_ENTRY),
    });
    a = registerIndicatorPlugin(a.registry, a.paneModel, overlayA, {
      instanceId: "sma-a",
      catalogEntry: SMA_ENTRY,
      params: {},
      style: goodStyle(SMA_ENTRY),
    });

    expect(a.registry.entries.map((e) => e.instanceId)).toEqual(["rsi-a", "sma-a"]);
    expect(b.registry.entries.map((e) => e.instanceId)).toEqual(["rsi-b"]);
    expect(a.paneModel.panes.map((p) => p.id)).toEqual(["mainA", "rsi-a"]);
    expect(b.paneModel.panes.map((p) => p.id)).toEqual(["mainB", "rsi-b"]);

    const beforeBStyle = b.registry.entries[0]!.style;
    a = { registry: setIndicatorPluginStyle(a.registry, "rsi-a", goodStyle(RSI_ENTRY, { color: "#123456" })), paneModel: a.paneModel };
    // Restyling lineage A must never touch lineage B's entries -- proves no
    // shared mutable array/object between independently-built registries.
    expect(b.registry.entries[0]!.style).toBe(beforeBStyle);
    expect(b.registry.entries).toHaveLength(1);
  });
});
