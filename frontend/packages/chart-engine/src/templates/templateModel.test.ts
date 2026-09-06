import { describe, expect, it } from "vitest";
import { addPane, createPaneModel } from "../panes/paneModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";
import type { ObjectTreeEntry } from "../legend/objectTree";
import { TEMPLATE_SCHEMA_VERSION, TemplateError, capture, decodeTemplate, encodeTemplate } from "./templateModel";
import type { Template } from "./templateModel";

function buildLayoutModel(): ChartLayoutModel {
  return {
    schemaVersion: 1,
    panels: [
      {
        id: "panel-1",
        instrument: { instrumentId: "AAPL", venue: "NASDAQ", symbol: "AAPL" },
        timeframe: "1D",
        indicators: [
          { id: "ind.sma", params: { period: 20 } },
          { id: "ind.rsi", params: { period: 14 } },
        ],
        drawingSetId: "draw-1",
      },
    ],
    activePanelId: "panel-1",
    watchlists: [],
  };
}

function buildInventory(): readonly ObjectTreeEntry[] {
  return [
    { id: "ind.sma", kind: "indicator", paneId: "main", name: "SMA", visible: true, locked: false },
    { id: "ind.rsi", kind: "indicator", paneId: "sub-1", name: "RSI", visible: true, locked: false },
  ];
}

function buildPaneModel(): PaneModel {
  return addPane(createPaneModel("main"), "sub-1");
}

function expectTemplateError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(TemplateError);
    expect((err as TemplateError).code).toBe(code);
    return;
  }
  throw new Error("expected TemplateError to be thrown");
}

describe("capture", () => {
  it("captures indicator set + params + pane placement, and pane layout", () => {
    const template = capture(buildInventory(), buildPaneModel(), buildLayoutModel());
    expect(template.schemaVersion).toBe(TEMPLATE_SCHEMA_VERSION);
    expect(template.indicators).toEqual([
      { id: "ind.sma", paneId: "main", params: { period: 20 } },
      { id: "ind.rsi", paneId: "sub-1", params: { period: 14 } },
    ]);
    expect(template.panes).toEqual([
      { id: "main", kind: "main", heightRatio: 0.5 },
      { id: "sub-1", kind: "sub", heightRatio: 0.5 },
    ]);
  });

  it("throws no_active_panel when layoutModel.activePanelId is null", () => {
    const layoutModel = { ...buildLayoutModel(), activePanelId: null };
    expectTemplateError(() => capture(buildInventory(), buildPaneModel(), layoutModel), "no_active_panel");
  });

  it("throws inventory_missing_indicator when an indicator isn't in the inventory", () => {
    const inventory = buildInventory().filter((entry) => entry.id !== "ind.rsi");
    expectTemplateError(() => capture(inventory, buildPaneModel(), buildLayoutModel()), "inventory_missing_indicator");
  });
});

describe("encodeTemplate / decodeTemplate", () => {
  it("round-trips a captured template through JSON", () => {
    const template = capture(buildInventory(), buildPaneModel(), buildLayoutModel());
    const json = JSON.parse(JSON.stringify(encodeTemplate(template)));
    expect(decodeTemplate(json)).toEqual(template);
  });

  it("rejects a schema_version that isn't the current value", () => {
    const template = capture(buildInventory(), buildPaneModel(), buildLayoutModel());
    const json = JSON.parse(JSON.stringify(encodeTemplate(template))) as Record<string, unknown>;
    json.schemaVersion = 999;
    expectTemplateError(() => decodeTemplate(json), "schema_version");
  });

  it("rejects a missing schema_version", () => {
    expectTemplateError(() => decodeTemplate({ panes: [], indicators: [] }), "schema_version");
  });

  it("rejects duplicate pane ids on encode", () => {
    const template: Template = {
      schemaVersion: TEMPLATE_SCHEMA_VERSION,
      panes: [
        { id: "main", kind: "main", heightRatio: 0.5 },
        { id: "main", kind: "sub", heightRatio: 0.5 },
      ],
      indicators: [],
    };
    expectTemplateError(() => encodeTemplate(template), "duplicate_pane_id");
  });

  it("rejects unknown top-level fields on decode", () => {
    const template = capture(buildInventory(), buildPaneModel(), buildLayoutModel());
    const json = encodeTemplate(template) as Record<string, unknown>;
    json.extra = true;
    expectTemplateError(() => decodeTemplate(json), "field_unknown");
  });

  it("rejects a non-finite indicator param", () => {
    const json = {
      schemaVersion: TEMPLATE_SCHEMA_VERSION,
      panes: [{ id: "main", kind: "main", heightRatio: 1 }],
      indicators: [{ id: "ind.sma", paneId: "main", params: { period: Number.NaN } }],
    };
    expectTemplateError(() => decodeTemplate(json), "field_invalid");
  });
});
