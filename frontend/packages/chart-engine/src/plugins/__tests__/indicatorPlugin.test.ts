import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
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
