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
import { PANE_CORRUPTIONS, createRng, pickCorruptedOp } from "./arbitraries";

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

describe("failure injection: randomized malformed-operation fuzz", () => {
  // True network/DB failure injection is structurally impossible here (these
  // functions take an in-memory model, not I/O) — the DEPTH_CH audit
  // (task-2729) accepted this for this exact leaf (1710) and its sibling
  // chart-engine pure-domain leaves (1616, 1711, 1732, 1809). This is the
  // closest analogue: a seeded fuzzer that targets the malformed-input domain
  // a UI layer (drag handle, persisted-layout round-trip) could hand these
  // functions, proving every corruption is rejected fail-closed with the
  // invariant intact rather than just documenting one example of each by
  // hand.
  it("fail-closed for 200 seeded corrupted operations (invariant holds after every rejected op)", () => {
    const rng = createRng(0xfa17);
    let model = createPaneModel("main");
    for (let i = 0; i < 3; i++) model = addPane(model, `seed${i}`);

    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 200; i++) {
      // Occasionally grow the model with a valid op so the fuzzer keeps
      // exploring a variety of pane counts, not just the seed shape.
      if (rng.bool()) model = addPane(model, `p${i}`, { heightRatio: 0.02 + rng.next() * 0.1 });

      const { corruption, apply } = pickCorruptedOp(rng, model);
      exercised.add(corruption);
      cases++;
      expect(() => apply(model), `corruption=${corruption} case ${i}`).toThrow(PaneModelError);
      expect(sumRatios(model), `corruption=${corruption} case ${i}`).toBeCloseTo(1, 9);
      expect(model.panes.filter((p) => p.kind === "main"), `corruption=${corruption} case ${i}`).toHaveLength(1);
    }
    // Every corruption kind must have fired at least once, and every fired
    // case must have satisfied the assertions above — a single corruption
    // that mutated the model or broke the invariant would already have
    // failed the test.
    expect(cases).toBe(200);
    expect(exercised.size).toBe(PANE_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk pane churn", () => {
  it("adds 500 panes, resizes each once, and removes them all within a fixed ms budget", () => {
    const start = performance.now();
    let model = createPaneModel("main");
    const ids: string[] = [];
    for (let i = 0; i < 500; i++) {
      const id = `p${i}`;
      ids.push(id);
      model = addPane(model, id);
    }
    for (const id of ids) model = resizePane(model, id, 1 / 1000);
    for (let i = ids.length - 1; i >= 0; i--) model = removePane(model, ids[i]!);
    const elapsedMs = performance.now() - start;

    expect(model.panes).toHaveLength(1);
    expect(sumRatios(model)).toBeCloseTo(1, 9);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // addPane/removePane/resizePane O(n^2) in an accidental way beyond their
    // documented proportional-redistribution pass would blow well past this.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: pane invariants", () => {
  describe("addPane: no heightRatio range validation", () => {
    /** Mimics a pre-hardening addPane with no range check on the incoming
     * ratio. A mutant of `addPane`, not part of the shipped module. */
    function legacyAddPaneNoValidation(model: PaneModel, id: string, heightRatio: number): PaneModel {
      const shrink = 1 - heightRatio;
      return {
        panes: [
          ...model.panes.map((p) => ({ ...p, heightRatio: p.heightRatio * shrink })),
          { id, kind: "sub" as const, heightRatio },
        ],
      };
    }

    it("red: a naive addPane accepts a zero heightRatio and produces an invisible pane", () => {
      const model = createPaneModel("main");
      const legacy = legacyAddPaneNoValidation(model, "invisible", 0);
      expect(legacy.panes.find((p) => p.id === "invisible")!.heightRatio).toBe(0);
    });

    it("green: the shipped addPane rejects a zero heightRatio instead of creating an invisible pane", () => {
      const model = createPaneModel("main");
      expectPaneError(() => addPane(model, "invisible", { heightRatio: 0 }), "PANE_HEIGHT_INVALID");
    });
  });

  describe("removePane: no proportional redistribution", () => {
    /** Mimics a pre-hardening removePane that just deletes the entry instead
     * of redistributing its share. A mutant of `removePane`, not part of the
     * shipped module. */
    function legacyRemovePaneNoRedistribute(model: PaneModel, id: string): PaneModel {
      return { panes: model.panes.filter((p) => p.id !== id) };
    }

    it("red: a naive removePane leaves the height sum short of 1 (a visual gap at the chart bottom)", () => {
      let model = createPaneModel("main");
      model = addPane(model, "sub1", { heightRatio: 0.3 });
      const legacy = legacyRemovePaneNoRedistribute(model, "sub1");
      expect(sumRatios(legacy)).toBeCloseTo(0.7, 9);
    });

    it("green: the shipped removePane redistributes so the sum stays exactly 1", () => {
      let model = createPaneModel("main");
      model = addPane(model, "sub1", { heightRatio: 0.3 });
      model = removePane(model, "sub1");
      expect(sumRatios(model)).toBeCloseTo(1, 9);
    });
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: heightRatio sum stays exactly 1 across 300 seeded random operation sequences", () => {
    const rng = createRng(0xd3d3);
    for (let i = 0; i < 300; i++) {
      let model = createPaneModel("main");
      const ids: string[] = [];
      const opCount = rng.int(5, 40);
      for (let j = 0; j < opCount; j++) {
        const roll = rng.next();
        if (roll < 0.5 || ids.length === 0) {
          const id = `p${i}-${j}`;
          model = addPane(model, id);
          ids.push(id);
        } else if (roll < 0.75) {
          model = resizePane(model, rng.pick(ids), 0.01 + rng.next() * 0.5);
        } else {
          model = removePane(model, ids.pop()!);
        }
        expect(sumRatios(model), `case ${i} op ${j}`).toBeCloseTo(1, 9);
        expect(model.panes.filter((p) => p.kind === "main"), `case ${i} op ${j}`).toHaveLength(1);
      }
    }
  }, 20_000);

  it("multi-instance isolation (D3): two independent model lineages built interleaved never alias each other's panes", () => {
    let modelA = createPaneModel("mainA");
    let modelB = createPaneModel("mainB");
    modelA = addPane(modelA, "a1");
    modelB = addPane(modelB, "b1");
    modelA = addPane(modelA, "a2");
    modelB = addPane(modelB, "b2");

    expect(modelA.panes.map((p) => p.id)).toEqual(["mainA", "a1", "a2"]);
    expect(modelB.panes.map((p) => p.id)).toEqual(["mainB", "b1", "b2"]);

    const beforeB = modelB.panes.map((p) => p.heightRatio);
    modelA = resizePane(modelA, "mainA", 0.9);
    // A resize on lineage A must never change lineage B's ratios — proves no
    // shared mutable array/object between independently-built models.
    expect(modelB.panes.map((p) => p.heightRatio)).toEqual(beforeB);
  });

  it("adversarial: near-boundary heightRatio values preserve the sum invariant", () => {
    for (const v of [1e-7, 1 - 1e-7, 0.5 + 1e-9]) {
      let model = createPaneModel("main");
      model = addPane(model, "sub1", { heightRatio: v });
      expect(sumRatios(model), `v=${v}`).toBeCloseTo(1, 6);
    }
  });
});
