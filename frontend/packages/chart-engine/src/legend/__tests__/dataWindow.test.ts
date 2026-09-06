import { describe, expect, it } from "vitest";
import {
  DataWindowError,
  type IndicatorSeriesSnapshot,
  computeDataWindowRows,
  createIndicatorLastValueMarkStyle,
  createIndicatorTooltipStyle,
} from "../dataWindow";

function expectDataWindowError(fn: () => unknown, code: string): void {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(DataWindowError);
    expect((err as DataWindowError).code).toBe(code);
    return;
  }
  throw new Error("expected DataWindowError to be thrown");
}

function smaLike(id: string, values: readonly (number | undefined)[]): IndicatorSeriesSnapshot {
  return {
    id,
    figures: [{ key: "value", title: id, color: "#1677FF" }],
    result: values.map((v) => (v === undefined ? undefined : { value: v })),
  };
}

describe("computeDataWindowRows", () => {
  it("projects every figure of every indicator at the given dataIndex", () => {
    const macd: IndicatorSeriesSnapshot = {
      id: "MACD",
      figures: [
        { key: "macd", title: "MACD", color: "#1677FF" },
        { key: "signal", title: "Signal", color: "#F92855" },
      ],
      result: [{ macd: 1.2345, signal: 0.5 }],
    };
    const rows = computeDataWindowRows([smaLike("SMA", [101.5]), macd], 0);
    expect(rows).toEqual([
      { indicatorId: "SMA", outputKey: "value", label: "SMA", value: "101.5000", color: "#1677FF" },
      { indicatorId: "MACD", outputKey: "macd", label: "MACD", value: "1.2345", color: "#1677FF" },
      { indicatorId: "MACD", outputKey: "signal", label: "Signal", value: "0.5000", color: "#F92855" },
    ]);
  });

  it("DoD: 30 indicators show values simultaneously at one crosshair-resolved bar", () => {
    const indicators = Array.from({ length: 30 }, (_, i) => smaLike(`IND_${i}`, [i + 0.5]));
    const rows = computeDataWindowRows(indicators, 0);
    expect(rows).toHaveLength(30);
    expect(rows.every((row, i) => row.value === (i + 0.5).toFixed(4))).toBe(true);
  });

  it("negative: empty indicator list yields no rows", () => {
    expect(computeDataWindowRows([], 0)).toEqual([]);
  });

  it("negative: crosshair dataIndex outside every indicator's result range renders defaultValue", () => {
    const indicators = [smaLike("SMA", [1, 2, 3])];
    expect(computeDataWindowRows(indicators, -1)[0]!.value).toBe("n/a");
    expect(computeDataWindowRows(indicators, 99)[0]!.value).toBe("n/a");
  });

  it("negative: a missing point within range (undefined) also renders defaultValue, not a crash", () => {
    const indicators = [smaLike("SMA", [1, undefined, 3])];
    expect(computeDataWindowRows(indicators, 1, { defaultValue: "--" })[0]!.value).toBe("--");
  });

  it("negative: duplicate indicator id is rejected fail-closed", () => {
    expectDataWindowError(
      () => computeDataWindowRows([smaLike("SMA", [1]), smaLike("SMA", [2])], 0),
      "CHART_DATA_WINDOW_DUPLICATE_INDICATOR",
    );
  });
});

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
