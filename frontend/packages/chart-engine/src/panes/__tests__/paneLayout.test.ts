import { describe, expect, it } from "vitest";
import { addPane, createPaneModel, resizePane } from "../paneModel";
import { PaneLayoutError, computePaneRects, createPaneScaleSet } from "../paneLayout";

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
