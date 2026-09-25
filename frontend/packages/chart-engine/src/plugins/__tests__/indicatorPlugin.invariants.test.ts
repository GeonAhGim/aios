import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import { createIndicatorPluginRegistry, registerIndicatorPlugin, setIndicatorPluginStyle, unregisterIndicatorPlugin } from "../indicatorPlugin";
import { MACD_ENTRY, createRng, goodStyle } from "./arbitraries";
import { RSI_ENTRY, SMA_ENTRY } from "./indicatorPlugin.fixtures";

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
