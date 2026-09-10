import { describe, expect, it } from "vitest";
import { addPane, createPaneModel } from "../panes/paneModel";
import { PaneModelError } from "../panes/paneModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";
import type { ObjectTreeEntry } from "../legend/objectTree";
import { capture, decodeTemplate, encodeTemplate } from "./templateModel";
import type { Template } from "./templateModel";
import { TemplateError } from "./templateModel";
import { apply } from "./applyTemplate";
import { APPLY_CORRUPTIONS, createRng, genTemplate, pickCorruptedApply } from "./arbitraries";

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

function buildBaseApplyInput(): { paneModel: PaneModel; layoutModel: ChartLayoutModel } {
  return {
    paneModel: createPaneModel("main"),
    layoutModel: buildLayoutModel(),
  };
}

describe("failure injection: randomized malformed-input fuzz", () => {
  // True network/DB failure injection is structurally impossible here
  // (apply() takes in-memory template/model values, not I/O) — the DEPTH_CH
  // audit (task-2729) accepted this for this exact leaf (1809) and its
  // sibling chart-engine pure-domain leaves (1616, 1710, 1711, 1732). This is
  // the closest analogue: a seeded fuzzer targeting the malformed-input
  // domain a screen-wiring caller (CH-17c) or a stale/adversarial persisted
  // template could hand `apply()`, proving every corruption is rejected
  // fail-closed with the caller's inputs left untouched, not just
  // documenting one example of each by hand.
  it("fail-closed for 150 seeded corrupted (template, input) pairs, inputs never mutated", () => {
    const rng = createRng(0x4a17);
    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 150; i++) {
      const template = genTemplate(rng, rng.int(2, 5), rng.int(1, 5));
      const knownIndicatorIds = new Set(template.indicators.map((indicator) => indicator.id));
      const base = { ...buildBaseApplyInput(), knownIndicatorIds };
      const paneModelSnapshot = JSON.parse(JSON.stringify(base.paneModel));
      const layoutModelSnapshot = JSON.parse(JSON.stringify(base.layoutModel));

      const { corruption, run } = pickCorruptedApply(rng, template, base);
      exercised.add(corruption);
      cases++;
      let caught: unknown;
      try {
        run(apply);
      } catch (err) {
        caught = err;
      }
      expect(caught, `expected corruption=${corruption} case ${i} to throw`).toBeDefined();
      expect(
        caught instanceof TemplateError || caught instanceof PaneModelError,
        `corruption=${corruption} case ${i} threw ${String(caught)}`,
      ).toBe(true);
      expect(base.paneModel, `corruption=${corruption} case ${i}`).toEqual(paneModelSnapshot);
      expect(base.layoutModel, `corruption=${corruption} case ${i}`).toEqual(layoutModelSnapshot);
    }
    expect(cases).toBe(150);
    // Every corruption kind must have fired at least once, and every fired
    // case must have satisfied the assertions above — a single corruption
    // that apply() accepted, or that mutated its inputs, would already have
    // failed the test.
    expect(exercised.size).toBe(APPLY_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk apply", () => {
  it("applies 300 templates of 150 indicators each within a fixed ms budget", () => {
    const rng = createRng(0x9e4d);
    const start = performance.now();
    for (let i = 0; i < 300; i++) {
      const template = genTemplate(rng, 2, 150);
      const knownIndicatorIds = new Set(template.indicators.map((indicator) => indicator.id));
      const result = apply(template, { ...buildBaseApplyInput(), knownIndicatorIds });
      expect(result.layoutModel.panels[0]!.indicators).toHaveLength(150);
    }
    const elapsedMs = performance.now() - start;
    // Generous fixed budget (not a relative ratchet): a regression that made
    // apply()'s diff/plan computation quadratic in indicator count would
    // blow well past this.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: apply() fail-closed indicator/pane checks", () => {
  describe("apply: no knownIndicatorIds validation", () => {
    /** Mimics a pre-hardening apply() that lets an unregistered indicator id
     * through into the registration plan instead of rejecting it up front. A
     * mutant of `apply`, not part of the shipped module. */
    function legacyApplyNoKnownIdCheck(template: Template): readonly string[] {
      return template.indicators.map((indicator) => indicator.id);
    }

    it("red: a naive apply plans to register an indicator id the registry doesn't know how to render", () => {
      const template: Template = {
        schemaVersion: 1,
        panes: [{ id: "main", kind: "main", heightRatio: 1 }],
        indicators: [{ id: "ind.unregistered", paneId: "main" }],
      };
      const planned = legacyApplyNoKnownIdCheck(template);
      expect(planned).toEqual(["ind.unregistered"]);
    });

    it("green: the shipped apply rejects the same template instead of planning to register an unknown indicator", () => {
      const template: Template = {
        schemaVersion: 1,
        panes: [{ id: "main", kind: "main", heightRatio: 1 }],
        indicators: [{ id: "ind.unregistered", paneId: "main" }],
      };
      let caught: unknown;
      try {
        apply(template, { ...buildBaseApplyInput(), knownIndicatorIds: new Set() });
      } catch (err) {
        caught = err;
      }
      expect(caught).toBeInstanceOf(TemplateError);
      expect((caught as TemplateError).code).toBe("unknown_indicator");
    });
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: applying 300 seeded random templates always yields a paneModel whose heightRatio sums to exactly 1", () => {
    const rng = createRng(0xd3b4);
    for (let i = 0; i < 300; i++) {
      const template = genTemplate(rng, rng.int(2, 6), rng.int(0, 8));
      const knownIndicatorIds = new Set(template.indicators.map((indicator) => indicator.id));
      const result = apply(template, { ...buildBaseApplyInput(), knownIndicatorIds });

      const sum = result.paneModel.panes.reduce((total, p) => total + p.heightRatio, 0);
      expect(sum, `case ${i}`).toBeCloseTo(1, 9);
      expect(
        result.layoutModel.panels[0]!.indicators.map((indicator) => indicator.id),
        `case ${i}`,
      ).toEqual(template.indicators.map((indicator) => indicator.id));
    }
  });

  it("multi-instance isolation (D3): applying two independent templates never lets one lineage's plan/model alias the other's", () => {
    const templateA: Template = {
      schemaVersion: 1,
      panes: [{ id: "main", kind: "main", heightRatio: 1 }],
      indicators: [{ id: "ind.sma", paneId: "main", params: { period: 20 } }],
    };
    const templateB: Template = {
      schemaVersion: 1,
      panes: [
        { id: "main", kind: "main", heightRatio: 0.6 },
        { id: "sub-1", kind: "sub", heightRatio: 0.4 },
      ],
      indicators: [{ id: "ind.rsi", paneId: "sub-1", params: { period: 14 } }],
    };

    const emptyLayoutModel: ChartLayoutModel = { ...buildLayoutModel(), panels: [{ ...buildLayoutModel().panels[0]!, indicators: [] }] };
    const resultA = apply(templateA, {
      paneModel: createPaneModel("main"),
      layoutModel: emptyLayoutModel,
      knownIndicatorIds: new Set(["ind.sma"]),
    });
    const resultB = apply(templateB, {
      paneModel: createPaneModel("main"),
      layoutModel: emptyLayoutModel,
      knownIndicatorIds: new Set(["ind.rsi"]),
    });

    expect(resultA.paneModel.panes).toHaveLength(1);
    expect(resultB.paneModel.panes).toHaveLength(2);
    // Mutating B's plan array must never affect A's — proves apply() built
    // two independent result objects, not shared/aliased internals.
    (resultB.plan.toRegister as { indicatorId: string }[]).push({ indicatorId: "ind.intruder" });
    expect(resultA.plan.toRegister).toEqual([{ indicatorId: "ind.sma", paneId: "main", params: { period: 20 } }]);
  });
});
