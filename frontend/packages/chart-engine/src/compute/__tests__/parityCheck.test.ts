import { describe, expect, it, vi } from "vitest";

import type { IndicatorCatalogEntry } from "../../plugins/indicatorPlugin";
import { computeIndicatorSeries } from "../clientEngine";
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
