import { describe, expect, it } from "vitest";
import { createIndicatorLastValueMarkStyle, createIndicatorTooltipStyle } from "../dataWindowStyle";

describe("style bindings", () => {
  it("createIndicatorTooltipStyle always shows and forwards defaultValue", () => {
    const style = createIndicatorTooltipStyle({ defaultValue: "n/a" });
    expect(style.showRule).toBe("always");
    expect(style.legend?.defaultValue).toBe("n/a");
  });

  it("createIndicatorLastValueMarkStyle defaults to shown and can be disabled", () => {
    expect(createIndicatorLastValueMarkStyle().show).toBe(true);
    expect(createIndicatorLastValueMarkStyle(false).show).toBe(false);
  });
});
