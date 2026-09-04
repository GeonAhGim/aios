import { describe, expect, it } from "vitest";
import { createTimeScale } from "./timeScale";

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
