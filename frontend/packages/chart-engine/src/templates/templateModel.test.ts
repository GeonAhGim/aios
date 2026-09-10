import { describe, expect, it } from "vitest";
import { addPane, createPaneModel } from "../panes/paneModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";
import type { ObjectTreeEntry } from "../legend/objectTree";
import { TEMPLATE_SCHEMA_VERSION, TemplateError, capture, decodeTemplate, encodeTemplate } from "./templateModel";
import type { Template } from "./templateModel";
import { TEMPLATE_DECODE_CORRUPTIONS, createRng, genTemplate, pickCorruptedDecode } from "./arbitraries";

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

describe("failure injection: randomized malformed-document fuzz", () => {
  // True network/DB failure injection is structurally impossible here
  // (decodeTemplate takes an already-parsed JSON value, not I/O) — the
  // DEPTH_CH audit (task-2729) accepted this for this exact leaf (1809) and
  // its sibling chart-engine pure-domain leaves (1616, 1710, 1711, 1732).
  // This is the closest analogue: a seeded fuzzer that targets the
  // malformed-document domain a persisted/shared template (localStorage,
  // share link, server round-trip) could hand `decodeTemplate`, proving
  // every corruption is rejected fail-closed rather than just documenting
  // one example of each by hand.
  it("fail-closed for 200 seeded corrupted template documents", () => {
    const rng = createRng(0x7e47);
    let cases = 0;
    const exercised = new Set<string>();
    for (let i = 0; i < 200; i++) {
      const template = genTemplate(rng, rng.int(2, 5), rng.int(1, 6));
      const validJson = encodeTemplate(template) as Record<string, unknown>;
      const { corruption, json } = pickCorruptedDecode(rng, validJson);
      exercised.add(corruption);
      cases++;
      expect(() => decodeTemplate(json), `corruption=${corruption} case ${i}`).toThrow(TemplateError);
    }
    expect(cases).toBe(200);
    // Every corruption kind must have fired at least once, and every fired
    // case must have satisfied the assertion above — a single corruption
    // that decodeTemplate accepted would already have failed the test.
    expect(exercised.size).toBe(TEMPLATE_DECODE_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk capture/encode/decode", () => {
  it("captures, encodes, and decodes 300 templates of 200 indicators each within a fixed ms budget", () => {
    const rng = createRng(0x9e4c);
    const layoutModel = buildLayoutModel();
    const paneModel = buildPaneModel();

    const start = performance.now();
    for (let i = 0; i < 300; i++) {
      const template = genTemplate(rng, 2, 200);
      const json = JSON.parse(JSON.stringify(encodeTemplate(template)));
      const decoded = decodeTemplate(json);
      expect(decoded.indicators).toHaveLength(200);
    }
    const elapsedMs = performance.now() - start;

    // Generous fixed budget (not a relative ratchet): a regression that made
    // encode/decode quadratic in indicator count would blow well past this.
    expect(elapsedMs).toBeLessThan(2000);
    // capture() itself is exercised separately (small, fixed inventory) so
    // the loop above isn't skewed by an unrelated O(panels*inventory) scan.
    const captureStart = performance.now();
    for (let i = 0; i < 2000; i++) capture(buildInventory(), paneModel, layoutModel);
    expect(performance.now() - captureStart).toBeLessThan(2000);
  });
});

describe("gate red reproduction: decodeTemplate fail-closed field checks", () => {
  describe("decodeTemplate: no unknown-field rejection", () => {
    /** Mimics a pre-hardening decodeTemplate that silently drops fields it
     * doesn't recognize instead of rejecting the document. A mutant, not
     * part of the shipped module. */
    function legacyDecodeTemplateLenient(value: Record<string, unknown>): Template {
      return {
        schemaVersion: TEMPLATE_SCHEMA_VERSION,
        panes: (value.panes as Template["panes"]) ?? [],
        indicators: (value.indicators as Template["indicators"]) ?? [],
      };
    }

    it("red: a naive decode silently accepts an unrecognized field instead of rejecting the document", () => {
      const json = { schemaVersion: TEMPLATE_SCHEMA_VERSION, panes: [], indicators: [], futureField: "unsupported-by-this-client" };
      const legacy = legacyDecodeTemplateLenient(json);
      expect(legacy).toEqual({ schemaVersion: TEMPLATE_SCHEMA_VERSION, panes: [], indicators: [] });
    });

    it("green: the shipped decodeTemplate rejects the same document instead of silently dropping the field", () => {
      const json = { schemaVersion: TEMPLATE_SCHEMA_VERSION, panes: [], indicators: [], futureField: "unsupported-by-this-client" };
      expectTemplateError(() => decodeTemplate(json), "field_unknown");
    });
  });

  describe("encodeTemplate: no duplicate-pane-id rejection", () => {
    /** Mimics a pre-hardening encodeTemplate with no uniqueness check. A
     * mutant, not part of the shipped module. */
    function legacyEncodeTemplateNoDupCheck(template: Template): Record<string, unknown> {
      return { schemaVersion: template.schemaVersion, panes: template.panes, indicators: template.indicators };
    }

    it("red: a naive encode silently emits two panes sharing one id (an unrenderable layout)", () => {
      const template: Template = {
        schemaVersion: TEMPLATE_SCHEMA_VERSION,
        panes: [
          { id: "main", kind: "main", heightRatio: 0.5 },
          { id: "main", kind: "sub", heightRatio: 0.5 },
        ],
        indicators: [],
      };
      const legacy = legacyEncodeTemplateNoDupCheck(template);
      expect((legacy.panes as unknown[]).length).toBe(2);
    });

    it("green: the shipped encodeTemplate rejects the same template instead of emitting an ambiguous document", () => {
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
  });
});

describe("D3 — property-based round trip & multi-instance isolation", () => {
  it("property: capture -> encode -> JSON -> decode round-trips exactly for 300 seeded random templates", () => {
    const rng = createRng(0xd3a3);
    for (let i = 0; i < 300; i++) {
      const template = genTemplate(rng, rng.int(2, 6), rng.int(0, 8));
      const json = JSON.parse(JSON.stringify(encodeTemplate(template)));
      expect(decodeTemplate(json), `case ${i}`).toEqual(template);
    }
  });

  it("multi-instance isolation (D3): two independently captured templates never alias each other's panes/indicators", () => {
    const layoutA: ChartLayoutModel = {
      schemaVersion: 1,
      panels: [
        {
          id: "panel-a",
          instrument: { instrumentId: "AAPL", venue: "NASDAQ", symbol: "AAPL" },
          timeframe: "1D",
          indicators: [{ id: "ind.sma", params: { period: 20 } }],
          drawingSetId: "draw-a",
        },
      ],
      activePanelId: "panel-a",
      watchlists: [],
    };
    const layoutB: ChartLayoutModel = {
      schemaVersion: 1,
      panels: [
        {
          id: "panel-b",
          instrument: { instrumentId: "MSFT", venue: "NASDAQ", symbol: "MSFT" },
          timeframe: "1H",
          indicators: [{ id: "ind.rsi", params: { period: 14 } }],
          drawingSetId: "draw-b",
        },
      ],
      activePanelId: "panel-b",
      watchlists: [],
    };
    const inventoryA: readonly ObjectTreeEntry[] = [{ id: "ind.sma", kind: "indicator", paneId: "main", name: "SMA", visible: true, locked: false }];
    const inventoryB: readonly ObjectTreeEntry[] = [{ id: "ind.rsi", kind: "indicator", paneId: "main", name: "RSI", visible: true, locked: false }];

    const templateA = capture(inventoryA, createPaneModel("main"), layoutA);
    const templateB = capture(inventoryB, createPaneModel("main"), layoutB);

    expect(templateA.indicators).toEqual([{ id: "ind.sma", paneId: "main", params: { period: 20 } }]);
    expect(templateB.indicators).toEqual([{ id: "ind.rsi", paneId: "main", params: { period: 14 } }]);
    // A round trip of B must never mutate A's already-captured template —
    // proves no shared mutable array/object between independent captures.
    const jsonB = JSON.parse(JSON.stringify(encodeTemplate(templateB)));
    decodeTemplate(jsonB);
    expect(templateA.indicators).toEqual([{ id: "ind.sma", paneId: "main", params: { period: 20 } }]);
  });
});
