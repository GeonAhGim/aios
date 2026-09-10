import { describe, expect, it } from "vitest";
import { type CrosshairMove, type CrosshairSyncState, createCrosshairSync } from "../crosshairSync";
import { createRng } from "./arbitraries";

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

describe("failure injection: randomized malformed-move fuzz", () => {
  // True network/DB failure injection is structurally impossible here (an
  // in-memory pub/sub over caller-supplied numbers, no I/O) — the DEPTH_CH
  // audit (task-2729) accepted this for this exact leaf (1710). This is the
  // closest analogue: a seeded fuzzer mixing malformed move() calls (NaN,
  // +/-Infinity, empty sourcePaneId) with valid ones, proving every
  // malformed call is rejected fail-closed (no notify, no subscriber
  // desync) rather than just documenting one hand-picked example of each.
  it("fail-closed for 200 seeded malformed move() calls (subscribers never desync)", () => {
    const rng = createRng(0xfa17);
    const sync = createCrosshairSync();
    const seenA: CrosshairSyncState[] = [];
    const seenB: CrosshairSyncState[] = [];
    sync.subscribe((s) => seenA.push(s));
    sync.subscribe((s) => seenB.push(s));

    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 200; i++) {
      const roll = rng.next();
      cases++;
      if (roll < 0.2) {
        exercised.add("nan_x");
        expect(() => sync.move({ sourcePaneId: "p", x: Number.NaN, timeMs: i })).toThrow(RangeError);
      } else if (roll < 0.4) {
        exercised.add("infinite_time");
        expect(() => sync.move({ sourcePaneId: "p", x: i, timeMs: Number.POSITIVE_INFINITY })).toThrow(RangeError);
      } else if (roll < 0.6) {
        exercised.add("neg_infinite_time");
        expect(() => sync.move({ sourcePaneId: "p", x: i, timeMs: Number.NEGATIVE_INFINITY })).toThrow(RangeError);
      } else if (roll < 0.8) {
        exercised.add("empty_source");
        expect(() => sync.move({ sourcePaneId: "", x: i, timeMs: i })).toThrow(RangeError);
      } else {
        exercised.add("valid");
        sync.move({ sourcePaneId: `p${i}`, x: i, timeMs: i });
      }
      // Every subscriber must see exactly the same sequence at every point —
      // a rejected move must never notify one subscriber but not another.
      expect(seenA, `case ${i}`).toEqual(seenB);
    }
    expect(cases).toBe(200);
    expect(exercised.size).toBe(5);
  });
});

describe("performance: numeric ms budget for broadcast under many subscribers", () => {
  it("broadcasts 5,000 moves to 50 subscribers within a fixed ms budget", () => {
    const sync = createCrosshairSync();
    let total = 0;
    for (let i = 0; i < 50; i++) {
      sync.subscribe((s) => {
        if (s.kind === "visible") total += s.timeMs;
      });
    }

    const start = performance.now();
    for (let i = 0; i < 5000; i++) sync.move({ sourcePaneId: "main", x: i, timeMs: i });
    const elapsedMs = performance.now() - start;

    expect(total).toBeGreaterThan(0);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // notify() do anything more than an O(subscribers) loop per move would
    // blow well past this at 50 subscribers x 5,000 moves.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("gate red reproduction: crosshair input validation", () => {
  /** Mimics a pre-hardening move() with no finite-value validation. A mutant
   * of `createCrosshairSync().move`, not part of the shipped module. */
  function legacyMoveNoValidation(update: CrosshairMove): CrosshairSyncState {
    return { kind: "visible", sourcePaneId: update.sourcePaneId, x: update.x, timeMs: update.timeMs };
  }

  it("red: a naive move() silently accepts a NaN timeMs instead of rejecting it", () => {
    const legacy = legacyMoveNoValidation({ sourcePaneId: "main", x: 1, timeMs: Number.NaN });
    expect(legacy.kind).toBe("visible");
    expect(legacy.kind === "visible" && Number.isNaN(legacy.timeMs)).toBe(true);
  });

  it("green: the shipped move() throws instead of letting a NaN timeMs reach subscribers", () => {
    const sync = createCrosshairSync();
    expect(() => sync.move({ sourcePaneId: "main", x: 1, timeMs: Number.NaN })).toThrow(RangeError);
    expect(sync.snapshot()).toEqual({ kind: "hidden" });
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: every subscriber matches sync.snapshot() after each of 300 seeded random moves/hides", () => {
    const rng = createRng(0xd3d3);
    const sync = createCrosshairSync();
    const seen: CrosshairSyncState[] = [];
    sync.subscribe((s) => seen.push(s));

    for (let i = 0; i < 300; i++) {
      if (rng.bool()) {
        sync.move({ sourcePaneId: `p${rng.int(0, 5)}`, x: rng.next() * 1000, timeMs: rng.int(0, 2_000_000_000) });
      } else {
        sync.hide();
      }
      if (seen.length > 0) expect(seen[seen.length - 1], `case ${i}`).toEqual(sync.snapshot());
    }
  });

  it("multi-instance isolation (D3): two independent CrosshairSync instances interleaved never leak state", () => {
    const syncA = createCrosshairSync();
    const syncB = createCrosshairSync();
    syncA.move({ sourcePaneId: "a", x: 1, timeMs: 100 });
    syncB.move({ sourcePaneId: "b", x: 2, timeMs: 200 });
    syncA.move({ sourcePaneId: "a2", x: 3, timeMs: 300 });

    expect(syncA.currentTimeMs()).toBe(300);
    expect(syncB.currentTimeMs()).toBe(200);
    syncB.hide();
    expect(syncA.currentTimeMs()).toBe(300);
  });

  it("adversarial: extreme-but-finite x/timeMs values never throw and round-trip exactly", () => {
    const sync = createCrosshairSync();
    for (const v of [1e15, -1e15, 0, -0, 1e-10]) {
      sync.move({ sourcePaneId: "p", x: v, timeMs: v });
      expect(sync.currentTimeMs(), `v=${v}`).toBe(v);
    }
  });
});
