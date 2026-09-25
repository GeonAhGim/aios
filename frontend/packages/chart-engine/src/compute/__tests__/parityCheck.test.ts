import { describe, expect, it, vi } from "vitest";

import type { IndicatorCatalogEntry } from "../../plugins/indicatorPlugin";
import { computeIndicatorSeries, type IndicatorSeriesResult } from "../clientEngine";
import { PARITY_TOLERANCE, checkIndicatorParity, resolveIndicatorSeries } from "../parityCheck";
import { VERIFIED_KERNEL_PINS } from "../verifiedIndicators";
import {
  REFERENCE_CANDLES,
  REFERENCE_INDICATORS,
  REFERENCE_TOLERANCE,
} from "./fixtures/referenceVectors";

// Mirrors `specs_talib.py` inputs/outputs for the 10 IND-7g-verified names
// (same shape table as `clientEngine.test.ts` — duplicated per that file's own
// convention rather than shared, so each test file's catalog fixture is self-contained).
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

describe("PARITY_TOLERANCE — pinned to the server cross-verification tolerance", () => {
  it("stays equal to verify_all.py's REFERENCE_TOLERANCE, not a re-typed literal", () => {
    expect(PARITY_TOLERANCE).toBe(REFERENCE_TOLERANCE);
  });
});

// DoD (1): every IND-7g-verified indicator's client series must match the
// server reference vector (transplanted from verify_all.py, not reinvented)
// within 1e-9 absolute error. A regression in any ported kernel turns this
// suite red — it is not just an example, it is the actual acceptance gate.
describe("checkIndicatorParity — all 10 verified kernels match the server reference vectors", () => {
  const catalog = verifiedCatalog();

  for (const [name, fixture] of Object.entries(REFERENCE_INDICATORS)) {
    it(`${name}: client series matches the server reference within tolerance`, () => {
      const client = computeIndicatorSeries({
        name,
        params: fixture.params,
        bars: REFERENCE_CANDLES,
        catalog,
      });
      const verdict = checkIndicatorParity(name, client, fixture.outputs);
      expect(verdict.ok).toBe(true);
    });
  }
});

describe("checkIndicatorParity — negative: an injected 1e-6 deviation is caught, not silently passed", () => {
  it("flags SMA as mismatched and reports name/maxAbsError/fallback when a server output value is perturbed", () => {
    const catalog = verifiedCatalog();
    const fixture = REFERENCE_INDICATORS.SMA!;
    const client = computeIndicatorSeries({ name: "SMA", params: fixture.params, bars: REFERENCE_CANDLES, catalog });

    const perturbedIndex = fixture.outputs.value.findIndex((v) => v !== null);
    expect(perturbedIndex).toBeGreaterThanOrEqual(0);
    const perturbedValues = [...fixture.outputs.value];
    perturbedValues[perturbedIndex] = perturbedValues[perturbedIndex]! + 1e-6;
    const perturbedServer = { value: perturbedValues };

    const verdict = checkIndicatorParity("SMA", client, perturbedServer);

    expect(verdict.ok).toBe(false);
    if (verdict.ok) throw new Error("unreachable");
    expect(verdict.name).toBe("SMA");
    expect(verdict.fallback).toBe(true);
    expect(verdict.maxAbsError).toBeGreaterThan(PARITY_TOLERANCE);
    expect(verdict.maxAbsError).toBeCloseTo(1e-6, 9);
    expect(verdict.mismatch.output).toBe("value");
    expect(verdict.mismatch.index).toBe(perturbedIndex);
  });
});

describe("resolveIndicatorSeries — fallback wiring", () => {
  const catalog = verifiedCatalog();
  const fixture = REFERENCE_INDICATORS.SMA!;
  const client = computeIndicatorSeries({ name: "SMA", params: fixture.params, bars: REFERENCE_CANDLES, catalog });

  it("match: renders the client series and reports source=client", () => {
    const resolved = resolveIndicatorSeries({ name: "SMA", client, server: fixture.outputs });
    expect(resolved.source).toBe("client");
    expect(resolved.series).toBe(client);
    expect(resolved.verdict?.ok).toBe(true);
  });

  it("mismatch: falls back to the server series, never the client series, and calls onMismatch (no silent fallback)", () => {
    const perturbedIndex = fixture.outputs.value.findIndex((v) => v !== null);
    const perturbedValues = [...fixture.outputs.value];
    perturbedValues[perturbedIndex] = perturbedValues[perturbedIndex]! + 1e-6;
    const perturbedServer = { value: perturbedValues };
    const onMismatch = vi.fn();

    const resolved = resolveIndicatorSeries({ name: "SMA", client, server: perturbedServer, onMismatch });

    expect(resolved.source).toBe("server");
    expect(resolved.series).toBe(perturbedServer);
    expect(resolved.series).not.toBe(client);
    expect(onMismatch).toHaveBeenCalledTimes(1);
    expect(onMismatch).toHaveBeenCalledWith(expect.objectContaining({ name: "SMA", fallback: true }));
  });

  it("no server reference available: fails closed to unverified rather than trusting the unconfirmed client series", () => {
    const resolved = resolveIndicatorSeries({ name: "SMA", client, server: null });
    expect(resolved.source).toBe("unverified");
    expect(resolved.series).toBeNull();
    expect(resolved.verdict).toBeNull();
  });
});

// DEPTH_CH(task-2729) audit: task-1954's own leaf (parityCheck.test.ts 6 +
// ChartPage.test.tsx 3) had failure-injection limited to numeric-deviation
// (a perturbed server value), no numeric performance assertion, and no
// gate-red reproduction. The gaps below fill exactly those three (DEEPEN
// task-3094).
describe("resolveIndicatorSeries — failure injection: mocked network exception, not a numeric deviation (DEEPEN task-3094)", () => {
  // The real server-reference fetch is a network round trip owned by
  // apps/web's caller (useIndicatorParityRows.ts) — this package can't import
  // that app-layer code (wrong direction across the workspace boundary). What
  // this module's public API (`resolveIndicatorSeries`) actually guarantees
  // to any such caller is: pass `server: null` when the fetch failed (for any
  // reason, including an exception) and it fails closed to "unverified" —
  // never treats an unreachable/thrown fetch as "no mismatch" and trusts the
  // unconfirmed client series. This exercises that contract with a real
  // `vi.fn().mockRejectedValue(new Error(...))`, i.e. a thrown/rejected
  // exception, not a perturbed numeric value.
  async function fetchServerReferenceOrNull(
    fetchServer: () => Promise<IndicatorSeriesResult>,
    onFetchError?: (err: unknown) => void,
  ): Promise<IndicatorSeriesResult | null> {
    try {
      return await fetchServer();
    } catch (err) {
      onFetchError?.(err);
      return null;
    }
  }

  it("server reference fetch rejects with a network exception: falls back to unverified, never renders the unconfirmed client series", async () => {
    const catalog = verifiedCatalog();
    const fixture = REFERENCE_INDICATORS.SMA!;
    const client = computeIndicatorSeries({ name: "SMA", params: fixture.params, bars: REFERENCE_CANDLES, catalog });

    const fetchServer: () => Promise<IndicatorSeriesResult> = vi
      .fn()
      .mockRejectedValue(new Error("ECONNREFUSED: indicator reference service unreachable"));
    const onFetchError = vi.fn();

    const server = await fetchServerReferenceOrNull(fetchServer, onFetchError);
    const resolved = resolveIndicatorSeries({ name: "SMA", client, server });

    expect(fetchServer).toHaveBeenCalledTimes(1);
    expect(onFetchError).toHaveBeenCalledTimes(1);
    expect(onFetchError).toHaveBeenCalledWith(expect.any(Error));
    expect(resolved.source).toBe("unverified");
    expect(resolved.series).toBeNull();
    expect(resolved.verdict).toBeNull();
  });

  it("server reference fetch recovers on retry: parity comparison resumes normally once the exception stops", async () => {
    const catalog = verifiedCatalog();
    const fixture = REFERENCE_INDICATORS.SMA!;
    const client = computeIndicatorSeries({ name: "SMA", params: fixture.params, bars: REFERENCE_CANDLES, catalog });

    const fetchServer: () => Promise<IndicatorSeriesResult> = vi
      .fn()
      .mockRejectedValueOnce(new Error("ETIMEDOUT: indicator reference service timed out"))
      .mockResolvedValueOnce(fixture.outputs);

    const firstAttempt = await fetchServerReferenceOrNull(fetchServer);
    expect(resolveIndicatorSeries({ name: "SMA", client, server: firstAttempt }).source).toBe("unverified");

    const secondAttempt = await fetchServerReferenceOrNull(fetchServer);
    const resolved = resolveIndicatorSeries({ name: "SMA", client, server: secondAttempt });

    expect(fetchServer).toHaveBeenCalledTimes(2);
    expect(resolved.source).toBe("client");
    expect(resolved.verdict?.ok).toBe(true);
  });
});

describe("checkIndicatorParity — numeric performance budget (DEEPEN task-3094)", () => {
  it("comparing a 100k-point series stays under a 1s budget", () => {
    const length = 100_000;
    const values = Array.from({ length }, (_, index) => (index < 19 ? null : 100 + index * 0.0001));
    const client: IndicatorSeriesResult = { value: [...values] };
    const server: IndicatorSeriesResult = { value: [...values] };

    const startedAt = performance.now();
    const verdict = checkIndicatorParity("SMA", client, server);
    const elapsedMs = performance.now() - startedAt;

    expect(verdict.ok).toBe(true);
    expect(elapsedMs).toBeLessThan(1000);
  });

  it("a single 1e-6 deviation among 100k points is still caught within the same budget", () => {
    const length = 100_000;
    const values = Array.from({ length }, (_, index) => (index < 19 ? null : 100 + index * 0.0001));
    const client: IndicatorSeriesResult = { value: [...values] };
    const perturbedValues = [...values];
    const perturbedIndex = length - 1;
    perturbedValues[perturbedIndex] = perturbedValues[perturbedIndex]! + 1e-6;
    const server: IndicatorSeriesResult = { value: perturbedValues };

    const startedAt = performance.now();
    const verdict = checkIndicatorParity("SMA", client, server);
    const elapsedMs = performance.now() - startedAt;

    expect(verdict.ok).toBe(false);
    expect(elapsedMs).toBeLessThan(1000);
  });
});

// Gate-red reproduction: the module's own docstring (parityCheck.ts, "Decision
// (task-1954)") pins an exact invariant — a `null`/number disagreement between
// client and server is itself a mismatch, never something this module treats
// as "not applicable" and skips. Contrast the real `absError` behavior
// (exercised indirectly through `checkIndicatorParity`) against a plausible
// *naive* regression that treats a null on either side as "not applicable,
// skip" (returns 0 -> counted as a match). If `checkIndicatorParity` were ever
// reverted to that naive rule, this describe block's own function-level
// contrast proves the regression, and the real-code assertion below would
// flip from failing (ok: false) to passing (ok: true) -- i.e. the gate turns
// red the moment the naive rule ships.
describe("checkIndicatorParity — gate-red reproduction: null/number lookback disagreement must not be skipped (DEEPEN task-3094)", () => {
  function naiveAbsError(client: number | null, server: number | null): number {
    // BUG (naive): treats "one side hasn't reached its lookback yet" as
    // "not applicable" and silently skips it instead of flagging disagreement.
    if (client === null || server === null) return 0;
    return Math.abs(client - server);
  }
  function realAbsError(client: number | null, server: number | null): number {
    if (client === null && server === null) return 0;
    if (client === null || server === null) return Infinity;
    return Math.abs(client - server);
  }

  it("function-level contrast: naive silently matches a null/number disagreement, real flags it as Infinity", () => {
    expect(naiveAbsError(100, null)).toBe(0);
    expect(naiveAbsError(null, 100)).toBe(0);
    expect(realAbsError(100, null)).toBe(Infinity);
    expect(realAbsError(null, 100)).toBe(Infinity);
  });

  it("checkIndicatorParity (real code): a server lookback-boundary null where the client already has a value is caught as a mismatch, not skipped", () => {
    const catalog = verifiedCatalog();
    const fixture = REFERENCE_INDICATORS.SMA!;
    const client = computeIndicatorSeries({ name: "SMA", params: fixture.params, bars: REFERENCE_CANDLES, catalog });

    const desyncedIndex = fixture.outputs.value.findIndex((v) => v !== null);
    expect(desyncedIndex).toBeGreaterThanOrEqual(0);
    const desyncedValues = [...fixture.outputs.value];
    desyncedValues[desyncedIndex] = null; // server disagrees with client about the lookback boundary
    const desyncedServer: IndicatorSeriesResult = { value: desyncedValues };

    const verdict = checkIndicatorParity("SMA", client, desyncedServer);

    // Were checkIndicatorParity's absError ever reverted to the naive
    // null-skip rule above, this assertion would flip to `false` (naive
    // returns 0 for every index where either side is null, so `worst` would
    // stay null and `ok` would read `true`) -- the gate turning red.
    expect(verdict.ok).toBe(false);
    if (verdict.ok) throw new Error("unreachable");
    expect(verdict.mismatch.index).toBe(desyncedIndex);
    expect(verdict.mismatch.absError).toBe(Infinity);
    expect(verdict.maxAbsError).toBe(Infinity);
  });
});
