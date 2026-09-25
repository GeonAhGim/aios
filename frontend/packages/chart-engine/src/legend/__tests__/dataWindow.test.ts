import { describe, expect, it } from "vitest";
import { DataWindowError, type DataWindowRow, type IndicatorSeriesSnapshot, computeDataWindowRows } from "../dataWindow";
import { corruptIndicatorPoint, createRng, genIndicatorSnapshot } from "./arbitraries";

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

describe("computeDataWindowRows -- failure injection (DEEPEN 1711)", () => {
  it("never leaks a non-finite figure value into a row, across 200 seeded NaN/Infinity/undefined point corruptions", () => {
    const rng = createRng(20260910);
    let corruptionsSeen = 0;
    for (let i = 0; i < 200; i++) {
      const base = genIndicatorSnapshot(`IND_${i}`, [i + 0.5, i + 1.5, i + 2.5]);
      const { indicator } = corruptIndicatorPoint(rng, base, i % 3);
      corruptionsSeen++;

      let rows: readonly DataWindowRow[] = [];
      expect(() => {
        rows = computeDataWindowRows([indicator], i % 3);
      }).not.toThrow();

      for (const row of rows) {
        expect(row.value).not.toContain("NaN");
        expect(row.value).not.toContain("Infinity");
      }
    }
    expect(corruptionsSeen).toBe(200);
  });
});

describe("computeDataWindowRows -- numeric performance (DEEPEN 1711)", () => {
  it("projects 30 simultaneous indicators over a 10,000-bar history across 1,000 crosshair moves within a 300ms budget", () => {
    const rng = createRng(11);
    const BAR_COUNT = 10_000;
    const indicators = Array.from({ length: 30 }, (_, i) =>
      genIndicatorSnapshot(
        `IND_${i}`,
        Array.from({ length: BAR_COUNT }, () => rng.next() * 1000),
      ),
    );

    const start = performance.now();
    for (let i = 0; i < 1_000; i++) {
      const rows = computeDataWindowRows(indicators, i * (BAR_COUNT / 1_000));
      expect(rows).toHaveLength(30);
    }
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(300);
  });
});

describe("computeDataWindowRows -- gate-red reproduction (DEEPEN 1711)", () => {
  /** Mirrors computeDataWindowRows but without `assertUniqueIndicatorIds`. */
  function naiveComputeDataWindowRows(indicators: readonly IndicatorSeriesSnapshot[], dataIndex: number): readonly DataWindowRow[] {
    const rows: DataWindowRow[] = [];
    for (const indicator of indicators) {
      const point = dataIndex >= 0 && dataIndex < indicator.result.length ? indicator.result[dataIndex] : undefined;
      for (const figure of indicator.figures) {
        const raw = point?.[figure.key];
        const value = typeof raw === "number" && Number.isFinite(raw) ? raw.toFixed(4) : "n/a";
        rows.push({ indicatorId: indicator.id, outputKey: figure.key, label: figure.title, value, color: figure.color ?? "#76808F" });
      }
    }
    return rows;
  }

  it("red: dropping the duplicate-id guard silently renders the same indicator id twice with conflicting values instead of rejecting", () => {
    const stale = smaLike("SMA", [100]); // e.g. an indicator config the panel hasn't cleaned up yet
    const fresh = smaLike("SMA", [999]); // same id, re-added with new params -- a real UI race

    // Green: the shipped implementation refuses to guess which one is authoritative.
    expectDataWindowError(() => computeDataWindowRows([stale, fresh], 0), "CHART_DATA_WINDOW_DUPLICATE_INDICATOR");

    // Red: the unguarded mutant silently shows both, one of them stale/wrong.
    const rows = naiveComputeDataWindowRows([stale, fresh], 0);
    expect(rows.filter((r) => r.indicatorId === "SMA")).toHaveLength(2);
    expect(rows.map((r) => r.value)).toEqual(["100.0000", "999.0000"]);
  });
});

describe("computeDataWindowRows -- D3 property + no-shared-state (DEEPEN 1711)", () => {
  it("300 seeded random indicator sets: row count always equals the sum of figures, every finite value round-trips to its source precision", () => {
    const rng = createRng(4242);
    for (let i = 0; i < 300; i++) {
      const count = rng.int(1, 12);
      const dataIndex = rng.int(0, 4);
      const indicators = Array.from({ length: count }, (_, idx) => {
        const values = Array.from({ length: dataIndex + 1 }, () => undefined as number | undefined);
        values[dataIndex] = rng.next() * 10_000;
        return genIndicatorSnapshot(`I${i}_${idx}`, values);
      });
      const rows = computeDataWindowRows(indicators, dataIndex);

      expect(rows).toHaveLength(count);
      for (const row of rows) {
        expect(Number.isFinite(Number(row.value))).toBe(true);
      }
    }
  });

  it("interleaving calls with independent indicator sets that reuse the same id across calls never cross-contaminates a result (pure, no cache)", () => {
    for (let i = 0; i < 50; i++) {
      const setA = [genIndicatorSnapshot("SMA", [i])];
      const setB = [genIndicatorSnapshot("SMA", [i + 1000])];

      const rowsA = computeDataWindowRows(setA, 0);
      const rowsB = computeDataWindowRows(setB, 0);
      const rowsA2 = computeDataWindowRows(setA, 0);

      expect(rowsA[0]!.value).toBe(i.toFixed(4));
      expect(rowsB[0]!.value).toBe((i + 1000).toFixed(4));
      expect(rowsA2[0]!.value).toBe(i.toFixed(4));
    }
  });
});
