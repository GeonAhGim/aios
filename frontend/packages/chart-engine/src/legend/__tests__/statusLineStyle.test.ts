import { describe, expect, it } from "vitest";
import type { KLineData, NeighborData, TooltipLegend } from "../../core/klinecharts";
import { createCrosshairValueStyle, createStatusLineTooltipStyle } from "../statusLineStyle";

function neighbor(current: KLineData | null): NeighborData<KLineData | null> {
  return { prev: null, current, next: null };
}

describe("createStatusLineTooltipStyle", () => {
  it("always shows the status line and wires the template to buildStatusLineLegends", () => {
    const style = createStatusLineTooltipStyle({ defaultValue: "n/a" });
    expect(style.showRule).toBe("always");
    expect(style.legend?.defaultValue).toBe("n/a");

    const template = style.legend!.template as (data: NeighborData<KLineData | null>) => TooltipLegend[];
    const rendered = template(neighbor(null));
    const oLegend = rendered.find((l) => l.title === "O")!;
    expect(oLegend.value).toBe("n/a");
  });
});

describe("createCrosshairValueStyle", () => {
  it("shows axis value labels by default and can be turned off", () => {
    expect(createCrosshairValueStyle().horizontal?.text?.show).toBe(true);
    expect(createCrosshairValueStyle({ showAxisLabel: false }).vertical?.text?.show).toBe(false);
  });
});
