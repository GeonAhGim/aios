import { describe, expect, it } from "vitest";
import {
  ObjectTreeError,
  ObjectTreeStateError,
  type ObjectTreeEntry,
  type ObjectTreeSource,
  applyObjectTreeState,
  buildObjectTree,
  encodeObjectTreeState,
  moveEntry,
  setEntryLocked,
  setEntryVisible,
  sortByPersistedOrder,
} from "../objectTree";

function expectObjectTreeError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(ObjectTreeError);
    expect((err as ObjectTreeError).code).toBe(code);
    return;
  }
  throw new Error("expected ObjectTreeError to be thrown");
}

function fakeSource(): ObjectTreeSource {
  return {
    getIndicators: () => [
      { id: "SMA", paneId: "candle_pane", name: "SMA", visible: true, zLevel: 10 },
      { id: "RSI", paneId: "pane_1", name: "RSI", visible: false, zLevel: 20 },
    ],
    getOverlays: () => [{ id: "trend-1", paneId: "candle_pane", name: "trendLine", visible: true, zLevel: 5, lock: true }],
  };
}

describe("buildObjectTree", () => {
  it("combines indicators and overlays, ordered by vendor zLevel, with lock resolved per kind", () => {
    const tree = buildObjectTree(fakeSource(), new Set(["RSI"]));
    expect(tree.map((e) => e.id)).toEqual(["trend-1", "SMA", "RSI"]);
    expect(tree.find((e) => e.id === "trend-1")).toMatchObject({ kind: "overlay", locked: true, visible: true });
    expect(tree.find((e) => e.id === "SMA")).toMatchObject({ kind: "indicator", locked: false, visible: true });
    expect(tree.find((e) => e.id === "RSI")).toMatchObject({ kind: "indicator", locked: true, visible: false });
  });

  it("negative: empty source yields an empty tree", () => {
    expect(buildObjectTree({ getIndicators: () => [], getOverlays: () => [] })).toEqual([]);
  });

  it("negative: duplicate id across indicators and overlays is rejected fail-closed", () => {
    const source: ObjectTreeSource = {
      getIndicators: () => [{ id: "dup", paneId: "p", name: "dup", visible: true, zLevel: 0 }],
      getOverlays: () => [{ id: "dup", paneId: "p", name: "dup", visible: true, zLevel: 1, lock: false }],
    };
    expectObjectTreeError(() => buildObjectTree(source), "CHART_OBJECT_TREE_DUPLICATE_ID");
  });
});

describe("entry mutation", () => {
  const tree = buildObjectTree(fakeSource());

  it("moveEntry reorders and setEntryVisible/setEntryLocked flip one entry", () => {
    const reordered = moveEntry(tree, "RSI", 0);
    expect(reordered.map((e) => e.id)).toEqual(["RSI", "trend-1", "SMA"]);

    const hidden = setEntryVisible(tree, "SMA", false);
    expect(hidden.find((e) => e.id === "SMA")!.visible).toBe(false);
    expect(tree.find((e) => e.id === "SMA")!.visible).toBe(true); // original untouched (immutable)

    const locked = setEntryLocked(tree, "SMA", true);
    expect(locked.find((e) => e.id === "SMA")!.locked).toBe(true);
  });

  it("negative: mutating an unknown id throws NOT_FOUND", () => {
    expectObjectTreeError(() => setEntryVisible(tree, "missing", false), "CHART_OBJECT_TREE_NOT_FOUND");
    expectObjectTreeError(() => moveEntry(tree, "missing", 0), "CHART_OBJECT_TREE_NOT_FOUND");
  });

  it("negative: moveEntry rejects an out-of-range index", () => {
    expectObjectTreeError(() => moveEntry(tree, "SMA", tree.length), "CHART_OBJECT_TREE_INVALID_INDEX");
    expectObjectTreeError(() => moveEntry(tree, "SMA", -1), "CHART_OBJECT_TREE_INVALID_INDEX");
  });
});

describe("encode/apply round trip", () => {
  it("show/hide, order and lock all survive an encode -> decode -> rebuild round trip identically", () => {
    let tree: readonly ObjectTreeEntry[] = buildObjectTree(fakeSource());
    tree = moveEntry(tree, "RSI", 0);
    tree = setEntryVisible(tree, "SMA", false);
    tree = setEntryLocked(tree, "SMA", true);

    const state = encodeObjectTreeState(tree);
    // Rebuilding from the vendor source again (e.g. after reload) yields a
    // fresh, vendor-ordered tree that the persisted state then re-shapes.
    const rebuilt = applyObjectTreeState(buildObjectTree(fakeSource()), state);

    expect(rebuilt).toEqual(tree);
  });

  it("negative: a persisted state referencing an unknown id is rejected", () => {
    const state = encodeObjectTreeState(buildObjectTree(fakeSource()));
    const stale = { ...state, order: [...state.order, "ghost"] };
    expect(() => applyObjectTreeState(buildObjectTree(fakeSource()), stale)).toThrow(ObjectTreeStateError);
  });

  it("negative: a persisted state missing an id (coverage mismatch) is rejected", () => {
    const state = encodeObjectTreeState(buildObjectTree(fakeSource()));
    const partial = { ...state, order: state.order.slice(1) };
    try {
      applyObjectTreeState(buildObjectTree(fakeSource()), partial);
      throw new Error("expected ObjectTreeStateError");
    } catch (err) {
      expect(err).toBeInstanceOf(ObjectTreeStateError);
      expect((err as ObjectTreeStateError).code).toBe("CHART_OBJECT_TREE_STATE_COVERAGE_MISMATCH");
    }
  });
});

describe("sortByPersistedOrder (CH-16b)", () => {
  it("reorders entries covered by the persisted order, by that order", () => {
    const tree = buildObjectTree(fakeSource()); // natural order: trend-1, SMA, RSI
    const sorted = sortByPersistedOrder(tree, ["RSI", "trend-1", "SMA"]);
    expect(sorted.map((e) => e.id)).toEqual(["RSI", "trend-1", "SMA"]);
  });

  it("negative: ids the persisted order doesn't cover keep their natural relative order and sort after covered ids", () => {
    const tree = buildObjectTree(fakeSource()); // natural order: trend-1, SMA, RSI
    // Persisted order only knows about "RSI" (e.g. saved before SMA/trend-1 existed).
    const sorted = sortByPersistedOrder(tree, ["RSI"]);
    expect(sorted.map((e) => e.id)).toEqual(["RSI", "trend-1", "SMA"]);
  });

  it("negative: an empty persisted order is a no-op (natural order preserved)", () => {
    const tree = buildObjectTree(fakeSource());
    expect(sortByPersistedOrder(tree, []).map((e) => e.id)).toEqual(tree.map((e) => e.id));
  });

  it("negative: a stale id in the persisted order that no longer exists is silently ignored (never throws)", () => {
    const tree = buildObjectTree(fakeSource());
    expect(() => sortByPersistedOrder(tree, ["ghost", "RSI"])).not.toThrow();
    expect(sortByPersistedOrder(tree, ["ghost", "RSI"]).map((e) => e.id)).toEqual(["RSI", "trend-1", "SMA"]);
  });
});
