import { describe, expect, it } from "vitest";

import type { IndicatorCatalogEntry } from "../../plugins/indicatorPlugin";
import type { Bar } from "../clientEngine";
import {
  ClientEngineError,
  KERNEL_FACTORIES,
  computeIndicatorSeries,
  createClientIncrementalIndicator,
} from "../clientEngine";
import { VERIFIED_KERNEL_PINS } from "../verifiedIndicators";

/** Mirrors `specs_talib.py` inputs/outputs for the 10 IND-7g-verified names (BBANDS excluded, see verifiedIndicators.ts). */
const SPEC_SHAPES: Readonly<Record<string, { inputs: readonly string[]; outputs: readonly string[] }>> = {
  SMA: { inputs: ["close"], outputs: ["value"] },
  EMA: { inputs: ["close"], outputs: ["value"] },
  RSI: { inputs: ["close"], outputs: ["value"] },
  ATR: { inputs: ["high", "low", "close"], outputs: ["value"] },
  CCI: { inputs: ["high", "low", "close"], outputs: ["value"] },
  WILLR: { inputs: ["high", "low", "close"], outputs: ["value"] },
  MFI: { inputs: ["high", "low", "close", "volume"], outputs: ["value"] },
  MACD: { inputs: ["close"], outputs: ["macd", "signal", "hist"] },
  STOCH: { inputs: ["high", "low", "close"], outputs: ["slowk", "slowd"] },
  OBV: { inputs: ["close", "volume"], outputs: ["value"] },
};

function verifiedCatalog(): IndicatorCatalogEntry[] {
  return Object.entries(VERIFIED_KERNEL_PINS).map(([name, pin]) => ({
    name,
    tier: pin.tier,
    category: "test",
    version: "ind-v1",
    hash: pin.entryHash,
    inputs: SPEC_SHAPES[name]!.inputs,
    outputs: SPEC_SHAPES[name]!.outputs,
  }));
}

describe("KERNEL_FACTORIES / VERIFIED_KERNEL_PINS parity", () => {
  it("ports exactly the pinned IND-7g-verified names — no more, no fewer", () => {
    expect(Object.keys(KERNEL_FACTORIES).sort()).toEqual(Object.keys(VERIFIED_KERNEL_PINS).sort());
  });
});

describe("createClientIncrementalIndicator — whitelist gate", () => {
  it("refuses an indicator absent from the catalog (fail-closed)", () => {
    expect(() => createClientIncrementalIndicator("SMA", { timeperiod: 3 }, [])).toThrow(ClientEngineError);
    try {
      createClientIncrementalIndicator("SMA", { timeperiod: 3 }, []);
      throw new Error("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ClientEngineError);
      expect((err as ClientEngineError).code).toBe("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED");
    }
  });

  it("refuses an indicator whose catalog entry_hash no longer matches the pin", () => {
    const catalog = verifiedCatalog().map((e) => (e.name === "SMA" ? { ...e, hash: "0".repeat(64) } : e));
    expect(() => createClientIncrementalIndicator("SMA", { timeperiod: 3 }, catalog)).toThrow(ClientEngineError);
  });

  it("rejects a bar missing a required input for a verified indicator", () => {
    const indicator = createClientIncrementalIndicator("SMA", { timeperiod: 3 }, verifiedCatalog());
    expect(() => indicator.update({})).toThrow(ClientEngineError);
  });

  it("rejects an invalid (non-positive) period param", () => {
    expect(() => createClientIncrementalIndicator("SMA", { timeperiod: 0 }, verifiedCatalog())).toThrow(ClientEngineError);
    expect(() => createClientIncrementalIndicator("SMA", {}, verifiedCatalog())).toThrow(ClientEngineError);
  });
});

describe("SMA kernel — matches hand-computed windowed mean", () => {
  it("emits null until the window fills, then a re-summed mean per bar", () => {
    const indicator = createClientIncrementalIndicator("SMA", { timeperiod: 3 }, verifiedCatalog());
    const closes = [1, 2, 3, 4, 5, 6];
    const values = closes.map((close) => indicator.update({ close } satisfies Bar).value);
    expect(values).toEqual([null, null, 2, 3, 4, 5]);
  });
});

describe("OBV kernel — matches hand-computed running total", () => {
  it("adds volume on up bars, subtracts on down bars, holds on flat bars", () => {
    const indicator = createClientIncrementalIndicator("OBV", {}, verifiedCatalog());
    const bars: Bar[] = [
      { close: 10, volume: 100 },
      { close: 11, volume: 200 },
      { close: 9, volume: 150 },
      { close: 9, volume: 150 },
      { close: 12, volume: 300 },
    ];
    const values = bars.map((bar) => indicator.update(bar).value);
    expect(values).toEqual([100, 300, 150, 150, 450]);
  });
});

describe("CCI kernel — exact zero-division guard (TA-Lib rule, not an approximation)", () => {
  it("returns exactly 0 (never NaN) when the window has zero mean deviation", () => {
    const indicator = createClientIncrementalIndicator("CCI", { timeperiod: 3 }, verifiedCatalog());
    indicator.update({ high: 10, low: 10, close: 10 });
    indicator.update({ high: 10, low: 10, close: 10 });
    const last = indicator.update({ high: 10, low: 10, close: 10 });
    expect(last).toEqual({ value: 0 });
  });
});

describe("STOCH kernel — matches hand-computed %K/%D", () => {
  it("computes fastk/slowk/slowd with degenerate (size-1) smoothing windows", () => {
    const indicator = createClientIncrementalIndicator(
      "STOCH",
      { fastk_period: 2, slowk_period: 1, slowd_period: 1 },
      verifiedCatalog(),
    );
    expect(indicator.update({ high: 10, low: 8, close: 9 })).toEqual({ slowk: null, slowd: null });
    const second = indicator.update({ high: 11, low: 9, close: 10 });
    expect(second.slowk).toBeCloseTo((66 + 2 / 3), 6);
    expect(second.slowd).toBeCloseTo((66 + 2 / 3), 6);
  });
});

describe("computeIndicatorSeries", () => {
  it("returns one array per output, aligned 1:1 with the input bars", () => {
    const bars: Bar[] = [{ close: 1 }, { close: 2 }, { close: 3 }, { close: 4 }];
    const series = computeIndicatorSeries({
      name: "SMA",
      params: { timeperiod: 2 },
      bars,
      catalog: verifiedCatalog(),
    });
    expect(series.value).toEqual([null, 1.5, 2.5, 3.5]);
  });

  it("propagates the whitelist gate (fail-closed) through the batch path too", () => {
    expect(() =>
      computeIndicatorSeries({ name: "SMA", params: { timeperiod: 2 }, bars: [{ close: 1 }], catalog: [] }),
    ).toThrow(ClientEngineError);
  });
});
