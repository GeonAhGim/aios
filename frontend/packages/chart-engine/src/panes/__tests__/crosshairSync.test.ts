import { describe, expect, it } from "vitest";
import { type CrosshairSyncState, createCrosshairSync } from "../crosshairSync";

describe("crosshair sync: one pane moves, every pane sees the same time", () => {
  it("broadcasts an identical timeMs to every subscribed pane", () => {
    const sync = createCrosshairSync();
    const seenByMain: CrosshairSyncState[] = [];
    const seenBySub1: CrosshairSyncState[] = [];
    const seenBySub2: CrosshairSyncState[] = [];
    sync.subscribe((s) => seenByMain.push(s));
    sync.subscribe((s) => seenBySub1.push(s));
    sync.subscribe((s) => seenBySub2.push(s));

    sync.move({ sourcePaneId: "sub1", x: 42, timeMs: 1_700_000_000_000 });

    for (const seen of [seenByMain, seenBySub1, seenBySub2]) {
      expect(seen).toHaveLength(1);
      expect(seen[0]).toEqual({ kind: "visible", sourcePaneId: "sub1", x: 42, timeMs: 1_700_000_000_000 });
    }
    expect(sync.currentTimeMs()).toBe(1_700_000_000_000);
  });

  it("a later move from a different pane still lands on the same timeMs for every subscriber", () => {
    const sync = createCrosshairSync();
    const seen: CrosshairSyncState[] = [];
    sync.subscribe((s) => seen.push(s));

    sync.move({ sourcePaneId: "main", x: 10, timeMs: 1000 });
    sync.move({ sourcePaneId: "sub1", x: 20, timeMs: 2000 });

    expect(seen).toHaveLength(2);
    expect(seen[1]).toEqual({ kind: "visible", sourcePaneId: "sub1", x: 20, timeMs: 2000 });
    expect(sync.currentTimeMs()).toBe(2000);
  });

  it("hide() notifies every pane and clears currentTimeMs", () => {
    const sync = createCrosshairSync();
    const seen: CrosshairSyncState[] = [];
    sync.subscribe((s) => seen.push(s));
    sync.move({ sourcePaneId: "main", x: 1, timeMs: 1 });

    sync.hide();

    expect(seen).toHaveLength(2);
    expect(seen[1]).toEqual({ kind: "hidden" });
    expect(sync.currentTimeMs()).toBeNull();
  });

  it("an unsubscribed listener receives no further updates", () => {
    const sync = createCrosshairSync();
    const seen: CrosshairSyncState[] = [];
    const unsubscribe = sync.subscribe((s) => seen.push(s));
    sync.move({ sourcePaneId: "main", x: 1, timeMs: 1 });
    unsubscribe();
    sync.move({ sourcePaneId: "main", x: 2, timeMs: 2 });

    expect(seen).toHaveLength(1);
  });
});

describe("negative", () => {
  it("rejects non-finite x/timeMs", () => {
    const sync = createCrosshairSync();
    expect(() => sync.move({ sourcePaneId: "main", x: Number.NaN, timeMs: 1 })).toThrow(RangeError);
    expect(() => sync.move({ sourcePaneId: "main", x: 1, timeMs: Number.POSITIVE_INFINITY })).toThrow(RangeError);
  });

  it("rejects an empty sourcePaneId", () => {
    const sync = createCrosshairSync();
    expect(() => sync.move({ sourcePaneId: "", x: 1, timeMs: 1 })).toThrow(RangeError);
  });

  it("hide() is a no-op (no notification) when already hidden", () => {
    const sync = createCrosshairSync();
    const seen: CrosshairSyncState[] = [];
    sync.subscribe((s) => seen.push(s));
    sync.hide();
    expect(seen).toHaveLength(0);
  });
});
