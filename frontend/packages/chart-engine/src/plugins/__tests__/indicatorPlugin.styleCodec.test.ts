import { describe, expect, it } from "vitest";
import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import {
  createIndicatorPluginRegistry,
  decodeIndicatorStyle,
  encodeIndicatorStyle,
  registerIndicatorPlugin,
  setIndicatorPluginStyle,
} from "../indicatorPlugin";
import { definition, expectPluginError, style } from "./indicatorPlugin.fixtures";

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
