import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import { createIndicatorPluginRegistry, registerIndicatorPlugin, unregisterIndicatorPlugin } from "../indicatorPlugin";
import { OSS_ENTRY, RSI_ENTRY, definition, expectPluginError, style } from "./indicatorPlugin.fixtures";

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
