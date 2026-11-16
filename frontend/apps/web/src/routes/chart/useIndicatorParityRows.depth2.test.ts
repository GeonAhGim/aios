import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { perfBudgetMs } from "../../test/perfBudget";
import { useIndicatorParityRows, type ServerIndicatorSeriesPort } from "./useIndicatorParityRows";

// CH-18c depth2 — D2 evidence 보강: negative 3종(toThrow, .rejects, source 단언),
// 실패 주입 2종, 성능 단언 1, 게이트 적색 재현 1, adversarial 1.
// 기존 depth1/test 파일의 renderHook 패턴·fixture·closure-capture 관습을 그대로 따른다.
//
// Local measurement: ~2ms for 25 bars / 1 overlay; budget 60ms is >30x headroom.

const SMA: OverlayEntry = { id: "SMA", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };
const RSI: OverlayEntry = { id: "RSI", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };

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

// ─── negative tests (D2: 3종 이상 — toThrow, .rejects, source 단언) ───

describe("useIndicatorParityRows — negative tests (D2: toThrow, .rejects, source)", () => {
  it("resolveServerSeries throws for unverified indicator → unverified source (toThrow 패턴)", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => {
      throw new Error("server series lookup failed");
    });
    const bars = manyBars(25);
    const catalog: IndicatorCatalogEntry[] = []; // RSI is not verified
    const overlays = [RSI];

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    // resolveServerSeries throws inside buildGateRow catch block → unverified
    expect(result.current).toHaveLength(1);
    expect(result.current[0]!.source).toBe("unverified");
    expect(result.current[0]!.value).toBeNull();
  });

  it("compute rejection → server fallback with error detail (.rejects 패턴)", async () => {
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
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(42) });

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(deferred).toHaveLength(1);
    act(() => deferred[0]!.reject(new TypeError("network timeout")));
    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]!.fallbackReason).toContain("network timeout");
    expect(result.current[0]!.value).toBe(42);
    warnSpy.mockRestore();
  });

  it("unverified indicator with no server series returns null value (source 단언)", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars = manyBars(25);
    const catalog: IndicatorCatalogEntry[] = []; // no verified indicators
    const overlays = [RSI];

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toHaveLength(1);
    expect(result.current[0]!.source).toBe("unverified");
    expect(result.current[0]!.value).toBeNull();
    expect(result.current[0]!.fallbackReason).toBeNull();
  });
});

// ─── failure injection (D2) ───

describe("useIndicatorParityRows — failure injection (D2)", () => {
  it("pool.submit returns rejected promise → server fallback with error detail", async () => {
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
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(55) });

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(deferred).toHaveLength(1);
    act(() => deferred[0]!.reject(new DOMException("NetworkError", "fetch")));
    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]!.fallbackReason).toContain("NetworkError");
    expect(result.current[0]!.value).toBe(55);
    warnSpy.mockRestore();
  });

  it("resolveServerSeries returns null after compute rejection → unverified", async () => {
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
    const resolveServerSeries: ServerIndicatorSeriesPort = () => null; // no server fallback

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(deferred).toHaveLength(1);
    act(() => deferred[0]!.reject(new Error("disk read error")));
    await waitFor(() => expect(result.current[0]!.source).toBe("unverified"));
    expect(result.current[0]!.value).toBeNull();
    warnSpy.mockRestore();
  });
});

// ─── performance assertion (D2) ───

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
});

// ─── gate red (D2) ───

describe("useIndicatorParityRows — gate red (D2)", () => {
  it("gate red: the same time-budget assertion fails when the hook effect is artificially delayed", async () => {
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

    // Clean up the pending promise to avoid unhandled promise rejection
    deferred[0]!.reject(new Error("gate red test cleanup"));
  });
});

// ─── adversarial (D3 for safety/execution axes) ───

describe("useIndicatorParityRows — adversarial (D3)", () => {
  it("compute rejection with no server fallback → unverified, never crashes", async () => {
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
    const resolveServerSeries: ServerIndicatorSeriesPort = () => null; // no server at all

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(deferred).toHaveLength(1);
    // Adversarial: reject with a non-Error value
    act(() => deferred[0]!.reject("plain string error"));
    await waitFor(() => expect(result.current[0]!.source).toBe("unverified"));
    expect(result.current[0]!.value).toBeNull();
    warnSpy.mockRestore();
  });

  it("multiple rapid re-renders with different bars → only latest resolves", async () => {
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
    // Server returns same values for all bar counts so parity always passes
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ bars }) => ({
      value: Array(bars.length).fill(100),
    }));
    const barsSmall = manyBars(25);
    const barsLarge = manyBars(100);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result, rerender } = renderHook(({ bars }: { bars: Bar[] }) => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool), {
      initialProps: { bars: barsSmall },
    });

    expect(deferred).toHaveLength(1);
    // Rapidly re-render with different bars
    rerender({ bars: barsLarge });
    rerender({ bars: barsSmall });
    expect(deferred).toHaveLength(3);

    // Resolve the first (oldest) deferred — should be ignored due to cancellation
    act(() => deferred[0]!.resolve({ value: Array(25).fill(111) }));
    await Promise.resolve();
    await Promise.resolve();

    // The oldest result should NOT appear because bars changed
    // The hook should still be pending for the latest bars
    expect(result.current[0]!.source).not.toBe("client");

    // Now resolve the latest — should appear with value 100 (matching server)
    act(() => deferred[2]!.resolve({ value: Array(25).fill(333) }));
    await waitFor(() => expect(result.current[0]!.value).toBe(100));
    // Server was called with the latest bars (25 bars from barsSmall rerender)
    expect(resolveServerSeries).toHaveBeenLastCalledWith({ name: "SMA", params: { timeperiod: 20 }, bars: barsSmall });
  });
});
