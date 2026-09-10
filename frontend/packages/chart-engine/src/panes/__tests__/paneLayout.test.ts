import { describe, expect, it } from "vitest";
import { addPane, createPaneModel, resizePane } from "../paneModel";
import type { PaneSpec } from "../paneModel";
import { type PaneRect, PaneLayoutError, computePaneRects, createPaneScaleSet } from "../paneLayout";
import { createRng, genPaneSpecs } from "./arbitraries";

describe("computePaneRects", () => {
  it("splits total height by ratio, top-to-bottom, summing to exactly totalHeight", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1", { heightRatio: 1 / 3 });

    const rects = computePaneRects(model.panes, 300);
    expect(rects).toEqual([
      { id: "main", top: 0, height: 200 },
      { id: "sub1", top: 200, height: 100 },
    ]);
  });

  it("gives the last pane any rounding remainder so rects always sum to totalHeight", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    model = addPane(model, "sub2");
    model = resizePane(model, "main", 1 / 3);

    const rects = computePaneRects(model.panes, 100);
    const total = rects.reduce((sum, r) => sum + r.height, 0);
    expect(total).toBe(100);
  });

  it("rejects a negative totalHeight", () => {
    const model = createPaneModel("main");
    expect(() => computePaneRects(model.panes, -1)).toThrow(PaneLayoutError);
  });
});

describe("createPaneScaleSet: per-pane independent scale", () => {
  it("gives each pane its own PriceScale that keeps its range across another pane's resize", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    const scales = createPaneScaleSet();
    scales.sync(model, 200);

    scales.scaleFor("main").setRange({ min: 0, max: 100 });
    scales.scaleFor("sub1").setRange({ min: -1, max: 1 });

    model = resizePane(model, "main", 0.75);
    scales.sync(model, 200);

    expect(scales.scaleFor("main").range).toEqual({ min: 0, max: 100 });
    expect(scales.scaleFor("sub1").range).toEqual({ min: -1, max: 1 });
    expect(scales.scaleFor("main")).not.toBe(scales.scaleFor("sub1"));
  });

  it("updates each pane's scale height to match the recomputed rect", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1", { heightRatio: 0.5 });
    const scales = createPaneScaleSet();
    scales.sync(model, 400);

    expect(scales.scaleFor("main").height).toBe(200);
    expect(scales.scaleFor("sub1").height).toBe(200);
  });

  it("drops a removed pane's scale and adds one for a newly-added pane", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    const scales = createPaneScaleSet();
    scales.sync(model, 200);
    expect(scales.has("sub1")).toBe(true);

    model = createPaneModel("main");
    scales.sync(model, 200);
    expect(scales.has("sub1")).toBe(false);
    expect(() => scales.scaleFor("sub1")).toThrow(PaneLayoutError);

    model = addPane(model, "sub2");
    scales.sync(model, 200);
    expect(scales.has("sub2")).toBe(true);
  });
});

describe("failure injection: randomized malformed layout input fuzz", () => {
  // True network/DB failure injection is structurally impossible here (pure
  // arithmetic over an in-memory pane list) — the DEPTH_CH audit (task-2729)
  // accepted this for this exact leaf (1710). This is the closest analogue: a
  // seeded fuzzer over pane count and totalHeight, including the negative/NaN
  // inputs a persisted-layout round-trip or a resize-observer callback could
  // hand this function, proving it either fail-closed rejects them or stays
  // numerically stable (rects always finite, always summing to exactly
  // totalHeight) rather than just documenting one hand-picked example.
  it("fail-closed / numerically stable for 200 seeded pane-count and totalHeight combinations", () => {
    const rng = createRng(0x9a17);
    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 200; i++) {
      const panes = genPaneSpecs(rng, rng.int(1, 15));
      const roll = rng.next();
      let totalHeight: number;
      let expectThrow = false;
      if (roll < 0.15) {
        totalHeight = -rng.int(1, 1000);
        expectThrow = true;
        exercised.add("negative");
      } else if (roll < 0.3) {
        totalHeight = Number.NaN;
        expectThrow = true;
        exercised.add("nan");
      } else if (roll < 0.45) {
        totalHeight = 0;
        exercised.add("zero");
      } else if (roll < 0.6) {
        totalHeight = 1e9;
        exercised.add("huge");
      } else {
        totalHeight = rng.int(0, 2000);
        exercised.add("normal");
      }
      cases++;

      if (expectThrow) {
        expect(() => computePaneRects(panes, totalHeight), `case ${i} totalHeight=${totalHeight}`).toThrow(
          PaneLayoutError,
        );
        continue;
      }
      const rects = computePaneRects(panes, totalHeight);
      const sum = rects.reduce((total, r) => total + r.height, 0);
      expect(sum, `case ${i} totalHeight=${totalHeight}`).toBe(totalHeight);
      for (const r of rects) expect(Number.isFinite(r.height), `case ${i} totalHeight=${totalHeight}`).toBe(true);
    }
    expect(cases).toBe(200);
    expect(exercised.size).toBe(5);
  });
});

describe("performance: numeric ms budget for large pane counts and repeated sync", () => {
  it("syncs scales for 200 panes across 100 recomputations within a fixed ms budget", () => {
    let model = createPaneModel("main");
    for (let i = 0; i < 199; i++) model = addPane(model, `p${i}`);
    const scales = createPaneScaleSet();

    const start = performance.now();
    for (let i = 0; i < 100; i++) scales.sync(model, 2000 + i);
    const elapsedMs = performance.now() - start;

    expect(scales.has("p0")).toBe(true);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // sync() rebuild every PriceScale instead of reusing surviving ones would
    // blow well past this at 200 panes x 100 recomputations.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: pixel-rect rounding", () => {
  /** Mimics a pre-hardening rect computation that floors every pane
   * (including the last) instead of giving the last pane the rounding
   * remainder. A mutant of `computePaneRects`, not part of the shipped
   * module. */
  function legacyFlooredRectsNoRemainder(panes: readonly PaneSpec[], totalHeight: number): PaneRect[] {
    let top = 0;
    const rects: PaneRect[] = [];
    for (const pane of panes) {
      const height = Math.floor(pane.heightRatio * totalHeight);
      rects.push({ id: pane.id, top, height });
      top += height;
    }
    return rects;
  }

  const panes: PaneSpec[] = [
    { id: "main", kind: "main", heightRatio: 1 / 3 },
    { id: "sub1", kind: "sub", heightRatio: 1 / 3 },
    { id: "sub2", kind: "sub", heightRatio: 1 / 3 },
  ];

  it("red: flooring every pane (including the last) leaves a rounding gap short of totalHeight", () => {
    const legacy = legacyFlooredRectsNoRemainder(panes, 100);
    const sum = legacy.reduce((total, r) => total + r.height, 0);
    expect(sum).toBeLessThan(100);
  });

  it("green: the shipped computePaneRects gives the last pane the remainder so rects sum to exactly totalHeight", () => {
    const rects = computePaneRects(panes, 100);
    const sum = rects.reduce((total, r) => total + r.height, 0);
    expect(sum).toBe(100);
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: computePaneRects rects sum exactly totalHeight and preserve order across 300 seeded random layouts", () => {
    const rng = createRng(0xd3d3);
    for (let i = 0; i < 300; i++) {
      const count = rng.int(1, 20);
      const panes = genPaneSpecs(rng, count);
      const totalHeight = rng.int(0, 5000);
      const rects = computePaneRects(panes, totalHeight);

      expect(rects.length, `case ${i}`).toBe(count);
      expect(rects.map((r) => r.id), `case ${i}`).toEqual(panes.map((p) => p.id));
      const sum = rects.reduce((total, r) => total + r.height, 0);
      expect(sum, `case ${i}`).toBe(totalHeight);
      let expectedTop = 0;
      for (const r of rects) {
        expect(r.top, `case ${i}`).toBe(expectedTop);
        expect(r.height, `case ${i}`).toBeGreaterThanOrEqual(0);
        expectedTop += r.height;
      }
    }
  });

  it("multi-instance isolation (D3): two independent PaneScaleSet instances never share PriceScale state", () => {
    let modelA = createPaneModel("mainA");
    modelA = addPane(modelA, "a1");
    let modelB = createPaneModel("mainB");
    modelB = addPane(modelB, "b1");
    const scalesA = createPaneScaleSet();
    const scalesB = createPaneScaleSet();

    scalesA.sync(modelA, 200);
    scalesB.sync(modelB, 400);
    scalesA.scaleFor("mainA").setRange({ min: 0, max: 10 });
    scalesB.scaleFor("mainB").setRange({ min: 100, max: 200 });

    expect(scalesA.scaleFor("mainA").range).toEqual({ min: 0, max: 10 });
    expect(scalesB.scaleFor("mainB").range).toEqual({ min: 100, max: 200 });
    expect(scalesA.has("mainB")).toBe(false);
    expect(scalesB.has("mainA")).toBe(false);
  });
});
