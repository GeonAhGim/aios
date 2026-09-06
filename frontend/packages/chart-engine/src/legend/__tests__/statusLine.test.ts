import { describe, expect, it } from "vitest";
import type { KLineData, NeighborData, TooltipLegend } from "../../core/klinecharts";
import { buildStatusLineLegends, createCrosshairValueStyle, createStatusLineTooltipStyle } from "../statusLine";

function neighbor(current: KLineData | null, prev: KLineData | null = null): NeighborData<KLineData | null> {
  return { prev, current, next: null };
}

function legendValueText(legend: TooltipLegend): string {
  return typeof legend.value === "string" ? legend.value : legend.value.text;
}

function findLegend(legends: readonly TooltipLegend[], title: string): TooltipLegend {
  const legend = legends.find((l) => l.title === title);
  if (!legend) throw new Error(`no legend titled "${title}"`);
  return legend;
}

describe("buildStatusLineLegends", () => {
  const candle: KLineData = { timestamp: 1_700_000_000_000, open: 100, high: 110, low: 95, close: 105, volume: 1234 };

  it("formats OHLCV plus signed change/change% against the previous close", () => {
    const prev: KLineData = { timestamp: 1_699_999_940_000, open: 90, high: 100, low: 88, close: 100 };
    const legends = buildStatusLineLegends(neighbor(candle, prev));

    expect(legendValueText(findLegend(legends, "O"))).toBe("100.00");
    expect(legendValueText(findLegend(legends, "H"))).toBe("110.00");
    expect(legendValueText(findLegend(legends, "L"))).toBe("95.00");
    expect(legendValueText(findLegend(legends, "C"))).toBe("105.00");
    expect(legendValueText(findLegend(legends, "Vol"))).toBe("1234");
    expect(legendValueText(findLegend(legends, "Chg"))).toBe("+5.00");
    expect(legendValueText(findLegend(legends, "Chg%"))).toBe("+5.00%");

    const changeLegend = findLegend(legends, "Chg");
    expect(typeof changeLegend.value).not.toBe("string");
    expect((changeLegend.value as { color: string }).color).toBe("#2DC08E");
  });

  it("falls back to the no-change color when there is no previous close to diff against", () => {
    const legends = buildStatusLineLegends(neighbor(candle, null));
    expect(legendValueText(findLegend(legends, "Chg"))).toBe("--");
    expect(legendValueText(findLegend(legends, "Chg%"))).toBe("--");
    expect((findLegend(legends, "Chg").value as { color: string }).color).toBe("#76808F");
  });

  it("uses downColor for a negative change", () => {
    const prev: KLineData = { timestamp: 0, open: 0, high: 0, low: 0, close: 200 };
    const legends = buildStatusLineLegends(neighbor(candle, prev));
    expect((findLegend(legends, "C").value as { color: string }).color).toBe("#F92855");
  });

  it("negative: no candle (empty data / crosshair resolved outside the series) renders every field as the placeholder", () => {
    const legends = buildStatusLineLegends(neighbor(null));
    expect(legends).toHaveLength(7);
    for (const legend of legends) expect(legendValueText(legend)).toBe("--");
  });

  it("honors a custom defaultValue and precision", () => {
    const legends = buildStatusLineLegends(neighbor(null), { defaultValue: "n/a" });
    expect(legendValueText(findLegend(legends, "O"))).toBe("n/a");

    const precise = buildStatusLineLegends(neighbor(candle), { pricePrecision: 4 });
    expect(legendValueText(findLegend(precise, "O"))).toBe("100.0000");
  });
});

describe("createStatusLineTooltipStyle", () => {
  it("always shows the status line and wires the template to buildStatusLineLegends", () => {
    const style = createStatusLineTooltipStyle({ defaultValue: "n/a" });
    expect(style.showRule).toBe("always");
    expect(style.legend?.defaultValue).toBe("n/a");

    const template = style.legend!.template as (data: NeighborData<KLineData | null>) => TooltipLegend[];
    const rendered = template(neighbor(null));
    expect(legendValueText(findLegend(rendered, "O"))).toBe("n/a");
  });
});

describe("createCrosshairValueStyle", () => {
  it("shows axis value labels by default and can be turned off", () => {
    expect(createCrosshairValueStyle().horizontal?.text?.show).toBe(true);
    expect(createCrosshairValueStyle({ showAxisLabel: false }).vertical?.text?.show).toBe(false);
  });
});
