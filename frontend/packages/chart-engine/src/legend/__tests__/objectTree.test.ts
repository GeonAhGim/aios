import { describe, expect, it } from "vitest";
import {
  ObjectTreeError,
  ObjectTreeStateError,
  type ObjectTreeEntry,
  type ObjectTreeSource,
  type ObjectTreeState,
  applyObjectTreeState,
  buildObjectTree,
  encodeObjectTreeState,
  moveEntry,
  setEntryLocked,
  setEntryVisible,
  sortByPersistedOrder,
} from "../objectTree";
import { createRng, genIndicatorSource, genOverlaySource, type Rng } from "./arbitraries";

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

function corruptZLevel(rng: Rng, zLevel: number): number {
  return rng.pick([zLevel, NaN, Infinity, -Infinity]);
}

describe("buildObjectTree -- failure injection (DEEPEN 1711)", () => {
  it("never throws on a non-finite vendor zLevel (NaN/Infinity/-Infinity), across 200 seeded corruptions, and never drops an entry", () => {
    const rng = createRng(20260910);
    let nonFiniteSeen = 0;
    for (let i = 0; i < 200; i++) {
      const indicators = Array.from({ length: 3 }, (_, idx) => {
        const src = genIndicatorSource(rng, `IND_${i}_${idx}`);
        const zLevel = corruptZLevel(rng, src.zLevel);
        if (!Number.isFinite(zLevel)) nonFiniteSeen++;
        return { ...src, zLevel };
      });
      const source: ObjectTreeSource = { getIndicators: () => indicators, getOverlays: () => [] };

      let tree: readonly ObjectTreeEntry[] = [];
      expect(() => {
        tree = buildObjectTree(source);
      }).not.toThrow();
      expect(tree).toHaveLength(3);
    }
    expect(nonFiniteSeen).toBeGreaterThan(0);
  });
});

describe("buildObjectTree -- numeric performance (DEEPEN 1711)", () => {
  it("builds a 10,000-object tree and runs 1,000 move/visibility/lock mutations within a 500ms budget", () => {
    const rng = createRng(13);
    const indicators = Array.from({ length: 5_000 }, (_, i) => genIndicatorSource(rng, `IND_${i}`));
    const overlays = Array.from({ length: 5_000 }, (_, i) => genOverlaySource(rng, `OVL_${i}`));
    const source: ObjectTreeSource = { getIndicators: () => indicators, getOverlays: () => overlays };

    const start = performance.now();
    let tree = buildObjectTree(source);
    expect(tree).toHaveLength(10_000);
    for (let i = 0; i < 1_000; i++) {
      const id = tree[i % tree.length]!.id;
      tree = setEntryVisible(tree, id, i % 2 === 0);
      tree = setEntryLocked(tree, id, i % 3 === 0);
      tree = moveEntry(tree, id, (i * 7) % tree.length);
    }
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(500);
  });
});

describe("applyObjectTreeState -- gate-red reproduction (DEEPEN 1711)", () => {
  /** Mirrors applyObjectTreeState but without the coverage-mismatch check. */
  function naiveApplyObjectTreeState(entries: readonly ObjectTreeEntry[], state: ObjectTreeState): readonly ObjectTreeEntry[] {
    const byId = new Map(entries.map((entry) => [entry.id, entry] as const));
    const hidden = new Set(state.hidden);
    const locked = new Set(state.locked);
    return state.order.filter((id) => byId.has(id)).map((id) => {
      const entry = byId.get(id)!;
      return { ...entry, visible: !hidden.has(id), locked: locked.has(id) };
    });
  }

  it("red: without the coverage check, an indicator added since the layout was saved is silently dropped from the rebuilt tree instead of forcing a re-derive", () => {
    const tree = buildObjectTree(fakeSource());
    const staleState = encodeObjectTreeState(tree); // saved before "new-indicator" existed

    const currentSource: ObjectTreeSource = {
      getIndicators: () => [...fakeSource().getIndicators(), { id: "new-indicator", paneId: "pane_1", name: "EMA", visible: true, zLevel: 30 }],
      getOverlays: () => fakeSource().getOverlays(),
    };
    const currentTree = buildObjectTree(currentSource);

    // Green: the shipped implementation refuses to silently lose "new-indicator".
    expect(() => applyObjectTreeState(currentTree, staleState)).toThrow(ObjectTreeStateError);

    // Red: the unguarded mutant silently rebuilds a 3-entry tree, dropping the 4th.
    const rebuilt = naiveApplyObjectTreeState(currentTree, staleState);
    expect(rebuilt).toHaveLength(3);
    expect(rebuilt.find((e) => e.id === "new-indicator")).toBeUndefined();
  });
});

describe("buildObjectTree/applyObjectTreeState -- D3 property + multi-instance isolation (DEEPEN 1711)", () => {
  it("300 seeded random source shapes: encode -> rebuild -> apply always round-trips order/visible/locked exactly", () => {
    const rng = createRng(4242);
    for (let i = 0; i < 300; i++) {
      const indicatorCount = rng.int(0, 5);
      const overlayCount = rng.int(0, 5);
      const indicators = Array.from({ length: indicatorCount }, (_, idx) => genIndicatorSource(rng, `I${i}_${idx}`));
      const overlays = Array.from({ length: overlayCount }, (_, idx) => genOverlaySource(rng, `O${i}_${idx}`));
      const source: ObjectTreeSource = { getIndicators: () => indicators, getOverlays: () => overlays };
      if (indicatorCount + overlayCount === 0) continue;

      let tree = buildObjectTree(source);
      if (tree.length > 1) tree = moveEntry(tree, tree[0]!.id, tree.length - 1);
      const state = encodeObjectTreeState(tree);
      const rebuilt = applyObjectTreeState(buildObjectTree(source), state);
      expect(rebuilt).toEqual(tree);
    }
  });

  it("two independent chart instances built from independent sources never leak a mutation across each other (immutability proof)", () => {
    const rng = createRng(55);
    let treeA = buildObjectTree({ getIndicators: () => [genIndicatorSource(rng, "A1"), genIndicatorSource(rng, "A2")], getOverlays: () => [] });
    let treeB = buildObjectTree({ getIndicators: () => [genIndicatorSource(rng, "A1"), genIndicatorSource(rng, "B2")], getOverlays: () => [] });
    const initialVisibleB = treeB.find((e) => e.id === "A1")!.visible;

    for (let i = 0; i < 30; i++) {
      // Interleave mutations across the two independent "instances", which happen to share id "A1".
      treeA = setEntryVisible(treeA, "A1", i % 2 === 0);
      treeB = setEntryLocked(treeB, "A1", i % 2 === 0);

      expect(treeA.find((e) => e.id === "A1")!.locked).toBe(false); // never touched by treeB's mutation
      expect(treeB.find((e) => e.id === "A1")!.visible).toBe(initialVisibleB); // never touched by treeA's mutation
    }
  });
});

/** Fisher-Yates: an unbiased O(n) shuffle. A `.sort(() => rng.next() - 0.5)` comparator violates the total-order contract sort relies on and can degrade to pathological (super-linear) comparison counts in some engines — unusable for a timed perf loop. */
function shuffled<T>(rng: Rng, items: readonly T[]): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = rng.int(0, i);
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}

// DEPTH_CH audit (task-2729): sortByPersistedOrder, introduced by task-2013
// (242f346, CH-16b), fell outside the buildObjectTree/applyObjectTreeState
// coverage DEEPEN task-3085 (1711) added — this block fills numeric
// performance, gate-red reproduction, and D3 for the CH-16b-specific function itself.
describe("sortByPersistedOrder -- numeric performance (DEEPEN 2013)", () => {
  it("2,000개 트리를 100개의 서로 다른 저장 순서로 재정렬해도 3s 예산 내에 끝난다", () => {
    const rng = createRng(20130910);
    const indicators = Array.from({ length: 1_000 }, (_, i) => genIndicatorSource(rng, `IND_${i}`));
    const overlays = Array.from({ length: 1_000 }, (_, i) => genOverlaySource(rng, `OVL_${i}`));
    const tree = buildObjectTree({ getIndicators: () => indicators, getOverlays: () => overlays });
    const ids = tree.map((e) => e.id);
    const orders = Array.from({ length: 100 }, () => shuffled(rng, ids));

    const start = performance.now();
    for (const order of orders) {
      const sorted = sortByPersistedOrder(tree, order);
      expect(sorted).toHaveLength(tree.length);
    }
    const elapsedMs = performance.now() - start;

    // Loose budget absorbing the shared-machine CPU contention documented in
    // vitest.config.ts (task-1968), but still catches sortByPersistedOrder
    // regressing from O(n log n) to O(n^2) (e.g. a linear scan instead of the
    // rank Map lookup) — over 20x the raw node measurement (~143ms) at this scale.
    expect(elapsedMs).toBeLessThan(3000);
  });
});

describe("sortByPersistedOrder -- gate-red reproduction (DEEPEN 2013)", () => {
  /** Mirrors sortByPersistedOrder but filters to only covered ids instead of appending the uncovered ones — the exact regression CH-16b exists to prevent (a newly added indicator must never vanish from the legend). */
  function naiveSortByPersistedOrder(entries: readonly ObjectTreeEntry[], order: readonly string[]): readonly ObjectTreeEntry[] {
    const byId = new Map(entries.map((e) => [e.id, e] as const));
    return order.filter((id) => byId.has(id)).map((id) => byId.get(id)!);
  }

  it("red: naive filter-based resort silently drops an indicator added after the order was saved; real sortByPersistedOrder keeps it", () => {
    const tree = buildObjectTree(fakeSource()); // trend-1, SMA, RSI
    const staleOrder = ["RSI", "SMA"]; // saved before trend-1 existed

    const naive = naiveSortByPersistedOrder(tree, staleOrder);
    expect(naive.map((e) => e.id)).toEqual(["RSI", "SMA"]);
    expect(naive.find((e) => e.id === "trend-1")).toBeUndefined();

    const real = sortByPersistedOrder(tree, staleOrder);
    expect(real).toHaveLength(3);
    expect(real.find((e) => e.id === "trend-1")).toBeDefined();
  });
});

describe("sortByPersistedOrder -- D3 property + multi-instance isolation (DEEPEN 2013)", () => {
  it("300 seeded random trees/orders: every entry present before sorting is still present after, regardless of order coverage", () => {
    const rng = createRng(20130911);
    for (let i = 0; i < 300; i++) {
      const indicatorCount = rng.int(0, 6);
      const overlayCount = rng.int(0, 6);
      const indicators = Array.from({ length: indicatorCount }, (_, idx) => genIndicatorSource(rng, `I${i}_${idx}`));
      const overlays = Array.from({ length: overlayCount }, (_, idx) => genOverlaySource(rng, `O${i}_${idx}`));
      const tree = buildObjectTree({ getIndicators: () => indicators, getOverlays: () => overlays });
      if (tree.length === 0) continue;

      const coverage = rng.int(0, tree.length);
      const persistedOrder = shuffled(
        rng,
        tree.slice(0, coverage).map((e) => e.id),
      );
      const sorted = sortByPersistedOrder(tree, persistedOrder);

      expect(sorted).toHaveLength(tree.length);
      expect(new Set(sorted.map((e) => e.id))).toEqual(new Set(tree.map((e) => e.id)));
    }
  });

  it("two independent panels' persisted orders never cross-contaminate a shared-shape tree", () => {
    const rng = createRng(20130912);
    const treeA = buildObjectTree({ getIndicators: () => [genIndicatorSource(rng, "SMA"), genIndicatorSource(rng, "RSI")], getOverlays: () => [] });
    const treeB = buildObjectTree({ getIndicators: () => [genIndicatorSource(rng, "SMA"), genIndicatorSource(rng, "RSI")], getOverlays: () => [] });

    const sortedA = sortByPersistedOrder(treeA, ["RSI", "SMA"]);
    const sortedB = sortByPersistedOrder(treeB, ["SMA", "RSI"]);

    expect(sortedA.map((e) => e.id)).toEqual(["RSI", "SMA"]);
    expect(sortedB.map((e) => e.id)).toEqual(["SMA", "RSI"]);
    // Sorting B's order after A must not retroactively change A's already-computed result (no shared mutable state).
    expect(sortedA.map((e) => e.id)).toEqual(["RSI", "SMA"]);
  });
});
