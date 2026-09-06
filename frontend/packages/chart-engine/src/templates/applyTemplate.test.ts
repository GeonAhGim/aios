import { describe, expect, it } from "vitest";
import { addPane, createPaneModel } from "../panes/paneModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";
import type { ObjectTreeEntry } from "../legend/objectTree";
import { capture, decodeTemplate, encodeTemplate } from "./templateModel";
import type { Template } from "./templateModel";
import { TemplateError } from "./templateModel";
import { apply } from "./applyTemplate";

function buildLayoutModel(): ChartLayoutModel {
  return {
    schemaVersion: 1,
    panels: [
      {
        id: "panel-1",
        instrument: { instrumentId: "AAPL", venue: "NASDAQ", symbol: "AAPL" },
        timeframe: "1D",
        indicators: [{ id: "ind.sma", params: { period: 20 } }],
        drawingSetId: "draw-1",
      },
    ],
    activePanelId: "panel-1",
    watchlists: [],
  };
}

describe("apply", () => {
  it("rejects a template referencing an unknown indicator id, and leaves input untouched", () => {
    const template: Template = {
      schemaVersion: 1,
      panes: [{ id: "main", kind: "main", heightRatio: 1 }],
      indicators: [{ id: "ind.unknown", paneId: "main" }],
    };
    const paneModel = createPaneModel("main");
    const layoutModel = buildLayoutModel();
    const paneModelSnapshot = JSON.parse(JSON.stringify(paneModel));
    const layoutModelSnapshot = JSON.parse(JSON.stringify(layoutModel));

    let caught: unknown;
    try {
      apply(template, { paneModel, layoutModel, knownIndicatorIds: new Set(["ind.sma"]) });
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(TemplateError);
    expect((caught as TemplateError).code).toBe("unknown_indicator");
    // Reference identity: apply() never mutates its inputs, so they're the exact same objects.
    expect(paneModel).toEqual(paneModelSnapshot);
    expect(layoutModel).toEqual(layoutModelSnapshot);
  });

  it("round-trips capture -> encode -> JSON -> decode -> apply: height ratios sum to 1, indicator order preserved", () => {
    const originalLayout: ChartLayoutModel = {
      schemaVersion: 1,
      panels: [
        {
          id: "panel-1",
          instrument: { instrumentId: "AAPL", venue: "NASDAQ", symbol: "AAPL" },
          timeframe: "1D",
          indicators: [
            { id: "ind.macd", params: { fast: 12, slow: 26 } },
            { id: "ind.sma", params: { period: 20 } },
          ],
          drawingSetId: "draw-1",
        },
      ],
      activePanelId: "panel-1",
      watchlists: [],
    };
    const paneModel: PaneModel = addPane(createPaneModel("main"), "sub-1");
    const inventory: readonly ObjectTreeEntry[] = [
      { id: "ind.macd", kind: "indicator", paneId: "sub-1", name: "MACD", visible: true, locked: false },
      { id: "ind.sma", kind: "indicator", paneId: "main", name: "SMA", visible: true, locked: false },
    ];

    const template = capture(inventory, paneModel, originalLayout);
    const json = JSON.parse(JSON.stringify(encodeTemplate(template)));
    const decoded = decodeTemplate(json);

    const result = apply(decoded, {
      paneModel,
      layoutModel: originalLayout,
      knownIndicatorIds: new Set(["ind.macd", "ind.sma"]),
    });

    const heightSum = result.paneModel.panes.reduce((total, p) => total + p.heightRatio, 0);
    expect(heightSum).toBe(1);
    expect(result.layoutModel.panels[0]!.indicators.map((indicator) => indicator.id)).toEqual([
      "ind.macd",
      "ind.sma",
    ]);
  });

  it("plans registration/unregistration as a diff against the current panel's indicators", () => {
    const layoutModel = buildLayoutModel();
    const paneModel = createPaneModel("main");
    const template: Template = {
      schemaVersion: 1,
      panes: [{ id: "main", kind: "main", heightRatio: 1 }],
      indicators: [{ id: "ind.rsi", paneId: "main", params: { period: 14 } }],
    };

    const result = apply(template, {
      paneModel,
      layoutModel,
      knownIndicatorIds: new Set(["ind.sma", "ind.rsi"]),
    });

    expect(result.plan.toRegister).toEqual([{ indicatorId: "ind.rsi", paneId: "main", params: { period: 14 } }]);
    expect(result.plan.toUnregister).toEqual(["ind.sma"]);
    expect(result.layoutModel.panels[0]!.indicators).toEqual([{ id: "ind.rsi", params: { period: 14 } }]);
  });

  it("rejects a template with no main pane", () => {
    const layoutModel = buildLayoutModel();
    const paneModel = createPaneModel("main");
    const template: Template = {
      schemaVersion: 1,
      panes: [{ id: "sub-1", kind: "sub", heightRatio: 1 }],
      indicators: [],
    };

    let caught: unknown;
    try {
      apply(template, { paneModel, layoutModel, knownIndicatorIds: new Set() });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TemplateError);
    expect((caught as TemplateError).code).toBe("field_invalid");
  });
});
