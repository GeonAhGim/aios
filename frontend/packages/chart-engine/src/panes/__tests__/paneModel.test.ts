import { describe, expect, it } from "vitest";
import {
  type PaneModel,
  PaneModelError,
  addPane,
  createPaneModel,
  mainPane,
  removePane,
  resizePane,
  setHeightRatios,
} from "../paneModel";

function expectPaneError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(PaneModelError);
    expect((err as PaneModelError).code).toBe(code);
    return;
  }
  throw new Error("expected PaneModelError to be thrown");
}

function sumRatios(model: PaneModel): number {
  return model.panes.reduce((total, p) => total + p.heightRatio, 0);
}

describe("add/remove round trip (5 panes)", () => {
  it("adding 4 sub-panes then removing them in reverse restores the original main-only model", () => {
    const start = createPaneModel("main");

    let model = start;
    for (const id of ["p1", "p2", "p3", "p4"]) {
      model = addPane(model, id);
    }
    expect(model.panes).toHaveLength(5);
    expect(sumRatios(model)).toBeCloseTo(1, 9);
    // Symmetric add: every pane (main included) ends up at an equal 1/5 share.
    for (const pane of model.panes) expect(pane.heightRatio).toBeCloseTo(0.2, 9);

    for (const id of ["p4", "p3", "p2", "p1"]) {
      model = removePane(model, id);
    }
    expect(model.panes.map((p) => p.id)).toEqual(["main"]);
    expect(model.panes[0]!.heightRatio).toBeCloseTo(1, 9);
    expect(sumRatios(model)).toBeCloseTo(1, 9);
  });

  it("resize then resize back restores the prior ratios", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    const before = model;

    model = resizePane(model, "main", 0.9);
    expect(sumRatios(model)).toBeCloseTo(1, 9);
    model = resizePane(model, "main", before.panes[0]!.heightRatio);

    expect(model.panes[0]!.heightRatio).toBeCloseTo(before.panes[0]!.heightRatio, 9);
    expect(model.panes[1]!.heightRatio).toBeCloseTo(before.panes[1]!.heightRatio, 9);
  });
});

describe("independent per-pane identity", () => {
  it("addPane never mutates the input model (immutability = independence across callers)", () => {
    const original = createPaneModel("main");
    const next = addPane(original, "sub1");
    expect(original.panes).toHaveLength(1);
    expect(next.panes).toHaveLength(2);
    expect(next).not.toBe(original);
  });

  it("mainPane finds the sole kind: \"main\" entry regardless of position", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    model = addPane(model, "sub2");
    expect(mainPane(model).id).toBe("main");
  });
});

describe("negative", () => {
  it("rejects removing a pane id that does not exist", () => {
    const model = createPaneModel("main");
    expectPaneError(() => removePane(model, "does-not-exist"), "PANE_NOT_FOUND");
  });

  it("rejects setHeightRatios whose sum is not 1", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    expectPaneError(() => setHeightRatios(model, { main: 0.5, sub1: 0.4 }), "PANE_HEIGHT_SUM_INVALID");
    expectPaneError(() => setHeightRatios(model, { main: 0.6, sub1: 0.6 }), "PANE_HEIGHT_SUM_INVALID");
  });

  it("refuses to remove the last (only) main pane", () => {
    const model = createPaneModel("main");
    expectPaneError(() => removePane(model, "main"), "PANE_LAST_MAIN_PANE");
  });

  it("rejects a duplicate pane id", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    expectPaneError(() => addPane(model, "sub1"), "PANE_DUPLICATE_ID");
    expectPaneError(() => addPane(model, "main"), "PANE_DUPLICATE_ID");
  });

  it("rejects an empty pane id", () => {
    expectPaneError(() => createPaneModel(""), "PANE_EMPTY_ID");
    expectPaneError(() => addPane(createPaneModel("main"), ""), "PANE_EMPTY_ID");
  });

  it("rejects a heightRatio outside (0, 1)", () => {
    const model = createPaneModel("main");
    expectPaneError(() => addPane(model, "sub1", { heightRatio: 0 }), "PANE_HEIGHT_INVALID");
    expectPaneError(() => addPane(model, "sub1", { heightRatio: 1 }), "PANE_HEIGHT_INVALID");
    expectPaneError(() => resizePane(model, "main", 1.5), "PANE_HEIGHT_INVALID");
  });

  it("setHeightRatios rejects a ratio set that does not cover exactly the model's pane ids", () => {
    let model = createPaneModel("main");
    model = addPane(model, "sub1");
    expectPaneError(() => setHeightRatios(model, { main: 1 }), "PANE_HEIGHT_INVALID");
    expectPaneError(() => setHeightRatios(model, { main: 0.5, ghost: 0.5 }), "PANE_NOT_FOUND");
  });
});
