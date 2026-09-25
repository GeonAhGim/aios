import { describe, expect, it } from "vitest";
import { buildStatusLineLegends, type StatusLineCandle, type StatusLineLegend, type StatusLineNeighbor } from "../statusLine";
import { corruptCandleField, createRng, genCandle } from "./arbitraries";

function neighbor(current: StatusLineCandle | null, prev: StatusLineCandle | null = null): StatusLineNeighbor<StatusLineCandle | null> {
  return { prev, current, next: null };
}

function legendValueText(legend: StatusLineLegend): string {
  return typeof legend.value === "string" ? legend.value : legend.value.text;
}

function findLegend(legends: readonly StatusLineLegend[], title: string): StatusLineLegend {
  const legend = legends.find((l) => l.title === title);
  if (!legend) throw new Error(`no legend titled "${title}"`);
  return legend;
}

describe("buildStatusLineLegends", () => {
  const candle: StatusLineCandle = { timestamp: 1_700_000_000_000, open: 100, high: 110, low: 95, close: 105, volume: 1234 };

  it("formats OHLCV plus signed change/change% against the previous close", () => {
    const prev: StatusLineCandle = { timestamp: 1_699_999_940_000, open: 90, high: 100, low: 88, close: 100 };
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
    const prev: StatusLineCandle = { timestamp: 0, open: 0, high: 0, low: 0, close: 200 };
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

describe("buildStatusLineLegends -- failure injection (DEEPEN 1711)", () => {
  it("never throws and never leaks NaN/Infinity into a legend value, across 200 seeded non-finite field corruptions", () => {
    const rng = createRng(20260910);
    let corruptionsSeen = 0;
    for (let i = 0; i < 200; i++) {
      const base = genCandle(rng, 1_700_000_000_000 + i * 60_000);
      const prevBase = genCandle(rng, 1_700_000_000_000 + i * 60_000 - 60_000);
      const { candle } = corruptCandleField(rng, base);
      const { candle: prev } = corruptCandleField(rng, prevBase);
      corruptionsSeen++;

      let legends: readonly StatusLineLegend[] = [];
      expect(() => {
        legends = buildStatusLineLegends(neighbor(candle, prev));
      }).not.toThrow();

      for (const legend of legends) {
        const text = legendValueText(legend);
        expect(text).not.toContain("NaN");
        expect(text).not.toContain("Infinity");
      }
    }
    expect(corruptionsSeen).toBe(200);
  });
});

describe("buildStatusLineLegends -- numeric performance (DEEPEN 1711)", () => {
  it("formats 50,000 crosshair-drag legend builds within a 500ms budget", () => {
    const rng = createRng(7);
    const candles = Array.from({ length: 500 }, (_, i) => genCandle(rng, i * 60_000));

    const start = performance.now();
    for (let i = 0; i < 50_000; i++) {
      const current = candles[i % candles.length]!;
      const prev = candles[(i + 1) % candles.length]!;
      buildStatusLineLegends(neighbor(current, prev));
    }
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(500);
  });
});

describe("buildStatusLineLegends -- gate-red reproduction (DEEPEN 1711)", () => {
  /**
   * Mirrors `formatFixed`/`formatSigned` but without the
   * `typeof value === "number" && Number.isFinite(value)` guard those
   * un-exported helpers apply before calling `.toFixed`. `volume` is an
   * optional field on `StatusLineCandle` (real instruments omit it), so
   * "no guard" is not a contrived input -- it is the single most ordinary
   * missing-data case this module exists to handle.
   */
  function naiveFormatFixed(value: number | undefined, precision: number): string {
    return value!.toFixed(precision);
  }

  it("red: an unguarded formatter crashes on the ordinary missing-volume case the shipped guard exists to absorb", () => {
    const candleNoVolume: StatusLineCandle = { timestamp: 0, open: 1, high: 2, low: 0.5, close: 1.5 };

    // Green: the shipped implementation's typeof/Number.isFinite guard.
    const legends = buildStatusLineLegends(neighbor(candleNoVolume));
    expect(legendValueText(findLegend(legends, "Vol"))).toBe("--");

    // Red: the same missing volume, run through the unguarded mutant.
    expect(() => naiveFormatFixed(candleNoVolume.volume, 0)).toThrow(TypeError);
  });
});

describe("buildStatusLineLegends -- D3 property + no-shared-state (DEEPEN 1711)", () => {
  it("300 seeded random candle pairs: change color always matches the sign of close-over-close, formatted O/H/L/C round-trip", () => {
    const rng = createRng(4242);
    for (let i = 0; i < 300; i++) {
      const prev = genCandle(rng, i * 60_000);
      const current = genCandle(rng, i * 60_000 + 60_000);
      const legends = buildStatusLineLegends(neighbor(current, prev));

      const changeAbs = current.close - prev.close;
      const expectedColor = changeAbs === 0 ? "#76808F" : changeAbs > 0 ? "#2DC08E" : "#F92855";
      expect((findLegend(legends, "Chg").value as { color: string }).color).toBe(expectedColor);
      expect((findLegend(legends, "C").value as { color: string }).color).toBe(expectedColor);

      for (const [title, expected] of [
        ["O", current.open],
        ["H", current.high],
        ["L", current.low],
      ] as const) {
        expect(Number(legendValueText(findLegend(legends, title)))).toBeCloseTo(expected, 2);
      }
    }
  });

  it("interleaving two independently-configured calls never leaks one call's options/colors into the other (no shared/cached state)", () => {
    const rng = createRng(99);
    const redForUp = { upColor: "#FF0000", downColor: "#00FF00" };
    const blueForUp = { upColor: "#0000FF", downColor: "#FFFF00" };

    for (let i = 0; i < 50; i++) {
      const prev = genCandle(rng, i * 60_000);
      const current: StatusLineCandle = { ...genCandle(rng, i * 60_000 + 60_000), close: prev.close + 1 }; // always "up"

      const legendsA = buildStatusLineLegends(neighbor(current, prev), redForUp);
      const legendsB = buildStatusLineLegends(neighbor(current, prev), blueForUp);
      // Re-run A after B to prove B's call didn't mutate any module-level default.
      const legendsA2 = buildStatusLineLegends(neighbor(current, prev), redForUp);

      expect((findLegend(legendsA, "Chg").value as { color: string }).color).toBe("#FF0000");
      expect((findLegend(legendsB, "Chg").value as { color: string }).color).toBe("#0000FF");
      expect((findLegend(legendsA2, "Chg").value as { color: string }).color).toBe("#FF0000");
    }
  });
});
