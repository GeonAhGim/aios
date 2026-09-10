import { describe, expect, it } from "vitest";
import { createPriceScale } from "../../core/priceScale";
import { createTimeScale } from "../../core/timeScale";
import { ScaleBindingError, bindScale } from "../scaleBinding";
import type { ScaleHint } from "../plotRenderers";
import { type ScaleCorruption, SCALE_CORRUPTIONS, createRng, pickScaleCorruption } from "./arbitraries";

function context() {
  const mainScale = createPriceScale({ range: { min: 0, max: 200 }, height: 100 });
  const ownScale = createPriceScale({ range: { min: 0, max: 100 }, height: 50 });
  const timeScale = createTimeScale({ range: { from: 0, to: 1000 }, width: 500 });
  return { mainScale, ownScale, timeScale };
}

describe("bindScale", () => {
  it("'overlay' delegates priceToY to the main pane scale", () => {
    const ctx = context();
    const projection = bindScale("overlay", ctx);
    expect(projection.priceToY(100)).toBe(ctx.mainScale.priceToY(100));
    expect(projection.priceToY(100)).not.toBe(ctx.ownScale.priceToY(100));
  });

  it("'own' and 'percent' both delegate priceToY to the indicator's own pane scale", () => {
    const ctx = context();
    for (const scale of ["own", "percent"] as const) {
      const projection = bindScale(scale, ctx);
      expect(projection.priceToY(50)).toBe(ctx.ownScale.priceToY(50));
    }
  });

  it("'inverted' mirrors the own-pane y coordinate across its height", () => {
    const ctx = context();
    const projection = bindScale("inverted", ctx);
    const plainY = ctx.ownScale.priceToY(25);
    expect(projection.priceToY(25)).toBe(ctx.ownScale.height - plainY);
  });

  it("'log' delegates to the own-pane scale for positive values", () => {
    const ctx = context();
    const projection = bindScale("log", ctx);
    expect(projection.priceToY(10)).toBe(ctx.ownScale.priceToY(10));
  });

  it("'log' rejects zero and negative values (negative)", () => {
    const ctx = context();
    const projection = bindScale("log", ctx);
    expect(() => projection.priceToY(0)).toThrow(ScaleBindingError);
    expect(() => projection.priceToY(-5)).toThrow(/SCALE_BINDING_NON_POSITIVE_FOR_LOG/);
  });

  it("timeToX always delegates to the shared time scale regardless of scale hint", () => {
    const ctx = context();
    const projection = bindScale("own", ctx);
    expect(projection.timeToX(500)).toBe(ctx.timeScale.timeToX(500));
  });

  it("rejects an unknown scale hint (negative)", () => {
    const ctx = context();
    expect(() => bindScale("nonsense" as ScaleHint, ctx)).toThrow(ScaleBindingError);
    try {
      bindScale("bogus" as ScaleHint, ctx);
    } catch (err) {
      expect((err as ScaleBindingError).code).toBe("SCALE_BINDING_UNKNOWN_SCALE");
    }
  });
});

function expectedCorruptionCode(corruption: ScaleCorruption): string {
  return corruption === "unknown_scale" ? "SCALE_BINDING_UNKNOWN_SCALE" : "SCALE_BINDING_NON_POSITIVE_FOR_LOG";
}

describe("failure injection: randomized malformed scale/value fuzz", () => {
  // bindScale is a pure coordinate mapper with no I/O, so mocked network/DB
  // failure injection is structurally impossible (per DEPTH_CH audit,
  // task-2729, for this exact leaf and its chart-engine pure-domain siblings).
  // This seeded fuzzer stands in for it: it targets the malformed-scale and
  // non-positive-log-value domain a backend PlotSpec or a corrupted layout
  // blob could hand this binder.
  it("fail-closed with the correct error code for 200 seeded corrupted scale/value pairs", () => {
    const ctx = context();
    const rng = createRng(0x5ca1e);
    const exercised = new Set<ScaleCorruption>();
    for (let i = 0; i < 200; i++) {
      const { corruption, scale, value } = pickScaleCorruption(rng);
      exercised.add(corruption);
      if (corruption === "unknown_scale") {
        expect(() => bindScale(scale as ScaleHint, ctx), `corruption=${corruption} case ${i}`).toThrow(ScaleBindingError);
        try {
          bindScale(scale as ScaleHint, ctx);
          expect.unreachable();
        } catch (err) {
          expect((err as ScaleBindingError).code, `case ${i}`).toBe(expectedCorruptionCode(corruption));
        }
      } else {
        const projection = bindScale("log", ctx);
        expect(() => projection.priceToY(value), `corruption=${corruption} case ${i} value=${value}`).toThrow(ScaleBindingError);
        try {
          projection.priceToY(value);
          expect.unreachable();
        } catch (err) {
          expect((err as ScaleBindingError).code, `case ${i}`).toBe(expectedCorruptionCode(corruption));
        }
      }
    }
    expect(exercised.size).toBe(SCALE_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk projection", () => {
  it("projects 100,000 price/time pairs across all 5 scale hints within a fixed ms budget", () => {
    const ctx = context();
    const projections = (["own", "overlay", "percent", "log", "inverted"] as const).map((s) => bindScale(s, ctx));

    const start = performance.now();
    let sum = 0;
    for (let i = 0; i < 100_000; i++) {
      const projection = projections[i % projections.length]!;
      sum += projection.priceToY(1 + (i % 90));
      sum += projection.timeToX(i);
    }
    const elapsedMs = performance.now() - start;

    expect(Number.isFinite(sum)).toBe(true);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // any hint's priceToY/timeToX allocate or recompute per-call state beyond
    // a direct delegation would blow well past this for 100,000 calls.
    expect(elapsedMs).toBeLessThan(1000);
  });
});

describe("gate red reproduction: log-scale domain validation", () => {
  /** Mimics a pre-hardening 'log' binding with no positivity check. A mutant
   * of `bindScale`, not part of the shipped module. */
  function legacyBindLogNoValidation(ctx: ScaleBindingContextLike): PlotProjectionLike {
    return { priceToY: (value: number) => ctx.ownScale.priceToY(value) };
  }

  it("red: a naive 'log' binding silently maps a non-positive value instead of rejecting it", () => {
    const ctx = context();
    const legacy = legacyBindLogNoValidation(ctx);
    expect(() => legacy.priceToY(-5)).not.toThrow();
    expect(Number.isFinite(legacy.priceToY(-5))).toBe(true);
  });

  it("green: the shipped 'log' binding throws SCALE_BINDING_NON_POSITIVE_FOR_LOG for a non-positive value", () => {
    const ctx = context();
    const projection = bindScale("log", ctx);
    expect(() => projection.priceToY(-5)).toThrow(ScaleBindingError);
  });
});

interface ScaleBindingContextLike {
  ownScale: { priceToY(value: number): number };
}
interface PlotProjectionLike {
  priceToY(value: number): number;
}

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: 300 seeded random positive values project deterministically (same input -> same output) across all hints", () => {
    const ctx = context();
    const rng = createRng(0xd3d3);
    for (const hint of ["own", "overlay", "percent", "log", "inverted"] as const) {
      for (let i = 0; i < 300; i++) {
        const value = 1 + rng.next() * 99;
        const projection = bindScale(hint, ctx);
        expect(projection.priceToY(value), `hint=${hint} case ${i}`).toBe(projection.priceToY(value));
      }
    }
  });

  it("multi-instance isolation (D3): two independent contexts with overlapping scale values never cross-project", () => {
    const ctxA = context();
    const ctxB = {
      mainScale: createPriceScale({ range: { min: 0, max: 50 }, height: 25 }),
      ownScale: createPriceScale({ range: { min: 0, max: 25 }, height: 10 }),
      timeScale: createTimeScale({ range: { from: 0, to: 200 }, width: 100 }),
    };

    const ownA = bindScale("own", ctxA);
    const ownB = bindScale("own", ctxB);
    // Interleave calls against both bindings; a shared-state bug would make
    // one binding's result depend on the other having been called first.
    const a1 = ownA.priceToY(50);
    const b1 = ownB.priceToY(12.5);
    const a2 = ownA.priceToY(50);
    const b2 = ownB.priceToY(12.5);

    expect(a1).toBe(a2);
    expect(b1).toBe(b2);
    expect(a1).not.toBe(b1);
  });

  it("adversarial: near-boundary positive values just above zero project without throwing on the log hint", () => {
    const ctx = context();
    const projection = bindScale("log", ctx);
    for (const value of [Number.MIN_VALUE, 1e-10, 1e-6]) {
      expect(() => projection.priceToY(value)).not.toThrow();
    }
  });
});
