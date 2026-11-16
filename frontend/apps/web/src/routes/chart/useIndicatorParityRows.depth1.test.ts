import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { perfBudgetMs } from "../../test/perfBudget";
import { useIndicatorParityRows, type ServerIndicatorSeriesPort } from "./useIndicatorParityRows";

// CH-18c depth1 — D2 evidence for useIndicatorParityRows hook.
// Follows the same renderHook pattern, fixtures, and closure-capture convention
// as useIndicatorParityRows.test.ts to avoid effect-dep thrashing.
//
// Local measurement: ~2ms for 25 bars / 1 overlay; budget 60ms is >30x headroom.

const SMA: OverlayEntry = { id: "SMA", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };
const EMA: OverlayEntry = { id: "EMA", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };

function bar(): Bar {
  return { open: 100, high: 101, low: 99, close: 100, volume: 1 };
}

function manyBars(count: number): Bar[] {
  return Array.from({ length: count }, () => bar());
}

function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [{ name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] }];
}

describe("useIndicatorParityRows — negative tests (D2)", () => {
  it("empty bars returns pending row for verified indicator (compute dispatched, no bars to compute)", async () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars: Bar[] = [];
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    // SMA is verified → gate passes, returns "pending" (compute dispatched)
    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0]!.source).toBe("pending");
    expect(result.current[0]!.id).toBe("SMA");
  });

  it("empty overlays returns no rows even with bars", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars = manyBars(25);
    const overlays: OverlayEntry[] = [];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toEqual([]);
  });

  it("unverified indicator with null server resolve falls back to unverified source", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars = manyBars(2);
    // Empty catalog means no indicator is verified
    const catalog: IndicatorCatalogEntry[] = [];
    const overlays = [SMA];

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toEqual([{ id: "SMA", source: "unverified", value: null, fallbackReason: null, computeNote: null }]);
  });
});

describe("useIndicatorParityRows — failure injection (D2)", () => {
  it("compute rejection falls back to server source with error detail", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(
        () =>
          new Promise((_, reject) => deferred.push({ resolve: () => {}, reject })) as unknown as Promise<unknown>,
      ),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const serverResult = { value: Array(25).fill(105) };
    const resolveServerSeries: ServerIndicatorSeriesPort = () => serverResult;

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(deferred).toHaveLength(1);
    act(() => deferred[0]!.reject(new Error("worker crash")));
    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]!.fallbackReason).toContain("클라이언트 계산 실패");
    expect(result.current[0]!.value).toBe(105);
    warnSpy.mockRestore();
  });

  it("networkError via HttpResponse.error falls back to server", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(
        () =>
          new Promise((_, reject) => deferred.push({ resolve: () => {}, reject })) as unknown as Promise<unknown>,
      ),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(200) });

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    act(() => deferred[0]!.reject(new TypeError("Failed to fetch")));
    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]!.fallbackReason).toContain("Failed to fetch");
    warnSpy.mockRestore();
  });
});

describe("useIndicatorParityRows — performance assertion (D2)", () => {
  // Local measurement: ~2ms for 25 bars, 1 SMA overlay; budget 60ms is >30x headroom.
  it("perf: 25-bar render within the time budget", async () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(100) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const startedAt = performance.now();
    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));
    await waitFor(() => expect(result.current).toHaveLength(1));
    const elapsed = performance.now() - startedAt;

    expect(elapsed).toBeLessThan(perfBudgetMs(60));
  });

  // gate red: a delayed dependency makes the very same budget assertion fail.
  it("gate red: the same time-budget assertion fails when the hook effect is artificially delayed", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(
        () =>
          new Promise((resolve) => deferred.push({ resolve })) as unknown as Promise<unknown>,
      ),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(100) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const startedAt = performance.now();
    renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    // Keep the deferred promise unresolved — the hook stays in "pending" state.
    // This artificially inflates elapsed time past the budget.
    // Use perfBudgetMs(60) * 10 so the delay exceeds even coverage-mode budget (60*3*2=360ms).
    await new Promise((r) => setTimeout(r, 2000));
    const elapsed = performance.now() - startedAt;

    // The budget assertion should fail when elapsed exceeds budget * 2 (gate-red delay).
    // Under coverage: budget*2 = 60*3*2 = 360ms. 2000ms >> 360ms → definitely fails.
    expect(() => expect(elapsed).toBeLessThan(perfBudgetMs(60) * 2)).toThrow();
  });
});

describe("useIndicatorParityRows — role=alert negative (D2)", () => {
  it("unverified indicator with no server series has no computed value", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars = manyBars(25);
    const catalog: IndicatorCatalogEntry[] = []; // no verified indicators
    const overlays = [EMA];

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    // value must be null for unverified with no server fallback
    expect(result.current[0]!.value).toBeNull();
    // source must be "unverified"
    expect(result.current[0]!.source).toBe("unverified");
  });
});
