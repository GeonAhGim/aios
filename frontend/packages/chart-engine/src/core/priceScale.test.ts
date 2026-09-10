import { describe, expect, it } from "vitest";
import { createPriceScale, type PriceScaleBackend } from "./priceScale";

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

describe("createPriceScale", () => {
  it("maps price <-> y inverted (higher price -> smaller y) across range and height", () => {
    const scale = createPriceScale({ range: { min: 0, max: 100 }, height: 200 });

    expect(scale.priceToY(100)).toBe(0);
    expect(scale.priceToY(50)).toBe(100);
    expect(scale.priceToY(0)).toBe(200);

    expect(scale.yToPrice(0)).toBe(100);
    expect(scale.yToPrice(100)).toBe(50);
    expect(scale.yToPrice(200)).toBe(0);
  });

  it("re-derives y positions after setRange/setHeight (resize)", () => {
    const scale = createPriceScale({ range: { min: 0, max: 10 }, height: 100 });
    expect(scale.priceToY(5)).toBe(50);

    scale.setHeight(400);
    expect(scale.priceToY(5)).toBe(200);

    scale.setRange({ min: 10, max: 20 });
    expect(scale.priceToY(15)).toBe(200);
  });

  it("returns 0 for priceToY and range max for yToPrice when height is 0", () => {
    const scale = createPriceScale({ range: { min: 0, max: 10 } });
    expect(scale.priceToY(5)).toBe(0);
    expect(scale.yToPrice(50)).toBe(10);
  });

  it("rejects a non-increasing range and a negative height", () => {
    expect(() => createPriceScale({ range: { min: 10, max: 10 } })).toThrow(RangeError);
    expect(() => createPriceScale({ range: { min: 10, max: 5 } })).toThrow(RangeError);
    const scale = createPriceScale();
    expect(() => scale.setHeight(-1)).toThrow(RangeError);
    expect(() => scale.setRange({ min: 5, max: 5 })).toThrow(RangeError);
  });
});

// DEEPEN(task-3069) of task-1375 (CH-1a, commit 87da8d1), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had negative-path
// coverage (RangeError on invalid range/height) but no failure injection, no
// numeric performance assertion, no gate-red reproduction, and no D3-level
// proof. This block fills those four gaps without changing priceScale.ts.
describe("createPriceScale — DEEPEN(task-3069): failure injection, perf, gate-red, D3", () => {
  it("실패 주입: a backend that throws on priceToY()/yToPrice() propagates the error instead of being silently swallowed", () => {
    const backend: PriceScaleBackend = {
      priceToY() {
        throw new Error("vendor pane not attached");
      },
      yToPrice() {
        throw new Error("vendor pane not attached");
      },
    };
    const scale = createPriceScale({ range: { min: 0, max: 100 }, height: 200, backend });

    expect(() => scale.priceToY(50)).toThrow(/vendor pane not attached/);
    expect(() => scale.yToPrice(100)).toThrow(/vendor pane not attached/);
  });

  it("게이트 적색 재현: a malformed backend answer (NaN/Infinity — the vendor equivalent of a corrupt network payload) is rejected by isResolved() and the linear fallback is used instead; deleting the Number.isFinite guard would leak NaN straight out of priceToY/yToPrice", () => {
    const backend: PriceScaleBackend = {
      priceToY: () => Number.NaN,
      yToPrice: () => Number.POSITIVE_INFINITY,
    };
    const scale = createPriceScale({ range: { min: 0, max: 100 }, height: 200, backend });

    expect(scale.priceToY(50)).toBe(100); // falls back to the linear mapping
    expect(scale.yToPrice(0)).toBe(100);
    expect(Number.isFinite(scale.priceToY(50))).toBe(true);
    expect(Number.isFinite(scale.yToPrice(0))).toBe(true);
  });

  it("실패 주입: a backend that resolves null for every call (e.g. an unmounted vendor pane) is fully transparent — output matches the no-backend linear mapping exactly", () => {
    const backend: PriceScaleBackend = { priceToY: () => null, yToPrice: () => null };
    const withBackend = createPriceScale({ range: { min: 0, max: 100 }, height: 200, backend });
    const withoutBackend = createPriceScale({ range: { min: 0, max: 100 }, height: 200 });

    for (const price of [0, 25, 50, 75, 100]) {
      expect(withBackend.priceToY(price)).toBe(withoutBackend.priceToY(price));
    }
  });

  it("수치 성능: 100,000 priceToY/yToPrice round-trips stay under a 600ms budget", () => {
    const scale = createPriceScale({ range: { min: 0, max: 1000 }, height: 500 });
    const startedAt = performance.now();
    for (let i = 0; i < 100_000; i++) {
      const y = scale.priceToY(i % 1000);
      scale.yToPrice(y);
    }
    const elapsedMs = performance.now() - startedAt;
    expect(elapsedMs).toBeLessThan(600);
  });

  it("어드버서리얼(D3): seeded fuzz over 2,000 random ranges/heights/prices — priceToY/yToPrice always round-trip within floating point tolerance and stay finite", () => {
    const rnd = mulberry32(20260910);
    for (let i = 0; i < 2000; i++) {
      const min = (rnd() - 0.5) * 20_000;
      const max = min + rnd() * 20_000 + 0.01; // always > min
      const height = rnd() * 5000;
      const scale = createPriceScale({ range: { min, max }, height });
      const price = min + rnd() * (max - min);

      const y = scale.priceToY(price);
      expect(Number.isFinite(y)).toBe(true);
      if (height > 0) {
        const roundTripped = scale.yToPrice(y);
        expect(roundTripped).toBeCloseTo(price, 6);
      }
    }
  });
});
