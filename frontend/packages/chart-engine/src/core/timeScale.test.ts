import { describe, expect, it } from "vitest";
import { createTimeScale, type TimeScaleBackend } from "./timeScale";

// Deterministic PRNG (mulberry32) for the seeded fuzz test below — no
// Math.random()/Date.now(), so a failure is always byte-for-byte reproducible.
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe("createTimeScale", () => {
  it("maps time <-> x linearly across the visible range and width", () => {
    const scale = createTimeScale({ range: { from: 0, to: 1000 }, width: 200 });

    expect(scale.timeToX(0)).toBe(0);
    expect(scale.timeToX(500)).toBe(100);
    expect(scale.timeToX(1000)).toBe(200);

    expect(scale.xToTime(0)).toBe(0);
    expect(scale.xToTime(100)).toBe(500);
    expect(scale.xToTime(200)).toBe(1000);
  });

  it("re-derives x positions after setRange/setWidth (resize)", () => {
    const scale = createTimeScale({ range: { from: 0, to: 100 }, width: 100 });
    expect(scale.timeToX(50)).toBe(50);

    scale.setWidth(400);
    expect(scale.timeToX(50)).toBe(200);

    scale.setRange({ from: 100, to: 200 });
    expect(scale.timeToX(150)).toBe(200);
  });

  it("returns 0 for timeToX and the range start for xToTime when width is 0", () => {
    const scale = createTimeScale({ range: { from: 10, to: 20 } });
    expect(scale.timeToX(15)).toBe(0);
    expect(scale.xToTime(50)).toBe(10);
  });

  it("rejects a non-increasing range and a negative width", () => {
    expect(() => createTimeScale({ range: { from: 100, to: 100 } })).toThrow(RangeError);
    expect(() => createTimeScale({ range: { from: 100, to: 50 } })).toThrow(RangeError);
    const scale = createTimeScale();
    expect(() => scale.setWidth(-1)).toThrow(RangeError);
    expect(() => scale.setRange({ from: 5, to: 5 })).toThrow(RangeError);
  });
});

// DEEPEN(task-3069) of task-1375 (CH-1a, commit 87da8d1), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had negative-path
// coverage (RangeError on invalid range/width) but no failure injection, no
// numeric performance assertion, no gate-red reproduction, and no D3-level
// proof. This block fills those four gaps without changing timeScale.ts.
describe("createTimeScale — DEEPEN(task-3069): failure injection, perf, gate-red, D3", () => {
  it("실패 주입: a backend that throws on timeToX()/xToTime() propagates the error instead of being silently swallowed", () => {
    const backend: TimeScaleBackend = {
      timeToX() {
        throw new Error("vendor pane not attached");
      },
      xToTime() {
        throw new Error("vendor pane not attached");
      },
    };
    const scale = createTimeScale({ range: { from: 0, to: 1000 }, width: 200, backend });

    expect(() => scale.timeToX(500)).toThrow(/vendor pane not attached/);
    expect(() => scale.xToTime(100)).toThrow(/vendor pane not attached/);
  });

  it("게이트 적색 재현: a malformed backend answer (NaN/Infinity — the vendor equivalent of a corrupt network payload) is rejected by isResolved() and the linear fallback is used instead; deleting the Number.isFinite guard would leak NaN straight out of timeToX/xToTime", () => {
    const backend: TimeScaleBackend = {
      timeToX: () => Number.NaN,
      xToTime: () => Number.NEGATIVE_INFINITY,
    };
    const scale = createTimeScale({ range: { from: 0, to: 1000 }, width: 200, backend });

    expect(scale.timeToX(500)).toBe(100); // falls back to the linear mapping
    expect(scale.xToTime(0)).toBe(0);
    expect(Number.isFinite(scale.timeToX(500))).toBe(true);
    expect(Number.isFinite(scale.xToTime(0))).toBe(true);
  });

  it("실패 주입: a backend that resolves null for every call (e.g. an unmounted vendor pane) is fully transparent — output matches the no-backend linear mapping exactly", () => {
    const backend: TimeScaleBackend = { timeToX: () => null, xToTime: () => null };
    const withBackend = createTimeScale({ range: { from: 0, to: 1000 }, width: 200, backend });
    const withoutBackend = createTimeScale({ range: { from: 0, to: 1000 }, width: 200 });

    for (const time of [0, 250, 500, 750, 1000]) {
      expect(withBackend.timeToX(time)).toBe(withoutBackend.timeToX(time));
    }
  });

  it("수치 성능: 100,000 timeToX/xToTime round-trips stay under a 600ms budget", () => {
    const scale = createTimeScale({ range: { from: 0, to: 100_000 }, width: 1000 });
    const startedAt = performance.now();
    for (let i = 0; i < 100_000; i++) {
      const x = scale.timeToX(i);
      scale.xToTime(x);
    }
    const elapsedMs = performance.now() - startedAt;
    expect(elapsedMs).toBeLessThan(600);
  });

  it("어드버서리얼(D3): seeded fuzz over 2,000 random ranges/widths/times — timeToX/xToTime always round-trip within floating point tolerance and stay finite", () => {
    const rnd = mulberry32(20260910);
    for (let i = 0; i < 2000; i++) {
      const from = (rnd() - 0.5) * 2_000_000;
      const to = from + rnd() * 2_000_000 + 1; // always > from
      const width = rnd() * 5000;
      const scale = createTimeScale({ range: { from, to }, width });
      const time = from + rnd() * (to - from);

      const x = scale.timeToX(time);
      expect(Number.isFinite(x)).toBe(true);
      if (width > 0) {
        const roundTripped = scale.xToTime(x);
        expect(roundTripped).toBeCloseTo(time, 3);
      }
    }
  });
});
