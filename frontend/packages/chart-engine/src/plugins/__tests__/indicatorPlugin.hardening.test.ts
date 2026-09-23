import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
import type { OverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import {
  IndicatorPluginError,
  createIndicatorPluginRegistry,
  registerIndicatorPlugin,
  setIndicatorPluginStyle,
  unregisterIndicatorPlugin,
  type IndicatorCatalogEntry,
  type IndicatorStyle,
} from "../indicatorPlugin";
import { goodStyle } from "./arbitraries";
import { RSI_ENTRY, SMA_ENTRY, definition, expectPluginError } from "./indicatorPlugin.fixtures";

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
