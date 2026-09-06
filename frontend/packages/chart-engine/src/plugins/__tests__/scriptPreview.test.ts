import { describe, expect, it } from "vitest";
import { createOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import { createIndicatorPluginRegistry } from "../indicatorPlugin";
import {
  SCRIPT_PREVIEW_PLOT_SPEC,
  clearScriptPreview,
  syncScriptPreview,
  type ScriptCompilePreviewResult,
} from "../scriptPreview";

function compileResult(overrides: Partial<ScriptCompilePreviewResult> = {}): ScriptCompilePreviewResult {
  return { scriptHash: "a".repeat(64), resources: { plotCount: 1 }, ...overrides };
}

describe("syncScriptPreview", () => {
  it("registers one sub-pane per plotCount, keyed off the scriptHash", () => {
    const overlay = createOverlayRegistry();
    const result = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    expect(result.instanceIds).toHaveLength(2);
    expect(result.registry.entries.map((e) => e.placement)).toEqual(["sub-pane", "sub-pane"]);
    // main pane + 2 registered sub-panes
    expect(result.paneModel.panes).toHaveLength(3);
    for (const id of result.instanceIds) {
      expect(result.plotSpecs.get(id)).toEqual(SCRIPT_PREVIEW_PLOT_SPEC);
    }
  });

  it("plotCount 0 tears down previous entries and registers none", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ resources: { plotCount: 0 } }),
    );

    expect(second.instanceIds).toEqual([]);
    expect(second.registry.entries).toEqual([]);
    expect(second.paneModel.panes).toHaveLength(1);
  });

  it("re-syncing with a new scriptHash replaces the old instances instead of accumulating", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ scriptHash: "a".repeat(64), resources: { plotCount: 1 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ scriptHash: "b".repeat(64), resources: { plotCount: 1 } }),
    );

    expect(second.instanceIds).toHaveLength(1);
    expect(second.instanceIds[0]).not.toEqual(first.instanceIds[0]);
    expect(second.registry.entries).toHaveLength(1);
    expect(second.paneModel.panes).toHaveLength(2);
  });

  it("recompiling with a smaller plotCount shrinks the sub-panes back down", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 3 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ resources: { plotCount: 1 } }),
    );

    expect(second.instanceIds).toHaveLength(1);
    expect(second.paneModel.panes).toHaveLength(2);
  });
});

describe("clearScriptPreview", () => {
  it("unregisters every previous instance and leaves only the main pane", () => {
    const overlay = createOverlayRegistry();
    const synced = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    const cleared = clearScriptPreview(synced.registry, synced.paneModel, synced.instanceIds);

    expect(cleared.registry.entries).toEqual([]);
    expect(cleared.paneModel.panes).toHaveLength(1);
    expect(cleared.paneModel.panes[0]?.kind).toBe("main");
  });

  it("is a no-op when there is nothing to clear", () => {
    const paneModel = createPaneModel("main");
    const registry = createIndicatorPluginRegistry();
    const cleared = clearScriptPreview(registry, paneModel, []);

    expect(cleared.registry).toEqual(registry);
    expect(cleared.paneModel).toEqual(paneModel);
  });
});

describe("SCRIPT_PREVIEW_PLOT_SPEC", () => {
  it("is a valid IND-15 PlotSpec (decoded, not hand-typed)", () => {
    expect(SCRIPT_PREVIEW_PLOT_SPEC).toEqual({
      kind: "line",
      scale: "own",
      default_pane: "separate",
      fill_between: null,
      color_rule: null,
      precision: null,
      legend_format: null,
    });
  });
});
