import { describe, expect, it } from "vitest";
import { createPriceScale } from "../../core/priceScale";
import { createTimeScale } from "../../core/timeScale";
import { ScaleBindingError, bindScale } from "../scaleBinding";
import type { ScaleHint } from "../plotRenderers";

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
