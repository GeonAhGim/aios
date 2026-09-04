import { describe, expect, it } from "vitest";
import { createPriceScale } from "./priceScale";

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
