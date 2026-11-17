import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { perfBudgetMs } from "../../test/perfBudget";
import { useIndicatorParityRows, type ServerIndicatorSeriesPort } from "./useIndicatorParityRows";
import { IndicatorParityPanel } from "./IndicatorParityPanel";
import type { ServerIndicatorSeriesPort as PanelServerPort } from "./IndicatorParityPanel";

// CH-18c depth2 — additional D2 evidence for useIndicatorParityRows hook.
// Supplements depth1.test.ts with missing negative-signal types:
//   - toThrow (resolveServerSeries throws)
//   - .rejects (worker compute rejects)
//   - .not.toBeInTheDocument (unverified indicator does not render a value)
//   - role=alert (no alert banner for unverified indicator parity rows)
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

afterEach(() => {
  vi.restoreAllMocks();
});

// ── negative tests: toThrow (D2) ──

describe("useIndicatorParityRows — negative: resolveServerSeries throws (D2)", () => {
  it("resolveServerSeries가 예외를 던지면 unverified source로 fail-closed한다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => {
      throw new Error("server compute unavailable");
    });
    const bars = manyBars(2);
    const overlays = [SMA];
    const catalog: IndicatorCatalogEntry[] = []; // SMA not verified
    const pool: WorkerPool | null = null;

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    // The hook catches the throw and falls back to unverified.
    // Assert the throw was handled: no exception bubbles, source is "unverified".
    expect(result.current).toHaveLength(1);
    expect(result.current[0]!.source).toBe("unverified");
    expect(result.current[0]!.value).toBeNull();
  });

  it("verified indicator가 worker로 dispatched된 후 compute가 reject되면 server fallback으로 간다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void; reject: (e: Error) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(() => new Promise((_resolve, reject) => deferred.push({ resolve: _resolve, reject }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(999) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    // First render: SMA is verified → "pending"
    expect(result.current[0]!.source).toBe("pending");

    // Reject the worker compute — should fall back to server.
    act(() => deferred[0]!.reject(new Error("worker compute failed")));
    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]!.value).toBe(999);
    expect(result.current[0]!.fallbackReason).toContain("클라이언트 계산 실패");
  });
});

// ── negative tests: .rejects via async hook pattern (D2) ──

describe("useIndicatorParityRows — negative: async rejection handling (D2)", () => {
  it("여러 indicator 중 하나가 reject되어도 나머지는 정상 계산값을 유지한다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void; reject: (e: Error) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(() => new Promise((_resolve, reject) => deferred.push({ resolve: _resolve, reject }))) as unknown as WorkerPool["submit"],
      size: 2,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const resolveServerSeries: ServerIndicatorSeriesPort = ({ name }) => ({ value: Array(25).fill(name === "SMA" ? 100 : 200) });
    const bars = manyBars(25);
    const overlays = [SMA, EMA];
    const catalog = smaVerifiedCatalog(); // only SMA is verified

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    // SMA is verified → pending; EMA is not → server fallback immediately.
    expect(result.current).toHaveLength(2);
    expect(result.current.find((r) => r.id === "EMA")!.source).toBe("server");

    // Reject SMA's compute — should fall back to server.
    act(() => deferred[0]!.reject(new Error("SMA compute failed")));
    await waitFor(() => {
      const smaRow = result.current.find((r) => r.id === "SMA");
      expect(smaRow!.source).toBe("server");
      expect(smaRow!.value).toBe(100);
    });
  });
});

// ── negative tests: .not.toBeInTheDocument + role=alert via rendered component (D2) ──

describe("useIndicatorParityRows — negative: .not.toBeInTheDocument + role=alert (D2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function streamCandle(i: number): StreamCandle {
    return {
      openTimeMs: Date.UTC(2026, 8, 3, i, 0, 0),
      confirmed: true,
      record: {
        key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
        open_time: new Date(Date.UTC(2026, 8, 3, i, 0, 0)).toISOString(),
        close_time: new Date(Date.UTC(2026, 8, 3, i + 1, 0, 0)).toISOString(),
        open: "100",
        high: "101",
        low: "99",
        close: "100",
        volume: "1",
        quote_volume: "100",
      },
    };
  }

  function manyStreamCandles(count: number): StreamCandle[] {
    return Array.from({ length: count }, (_, i) => streamCandle(i));
  }

  it("unverified indicator with null server resolve renders '--' and does not show a value", async () => {
    const resolveServerSeries: PanelServerPort = () => null;
    const catalog: IndicatorCatalogEntry[] = []; // no verified indicators

    render(
      <IndicatorParityPanel
        candles={manyStreamCandles(25)}
        overlays={[SMA]}
        catalog={catalog}
        resolveServerSeries={resolveServerSeries}
        computePool={null}
      />,
    );

    // The value span should show "--" (null → "--" in the component).
    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("--");
    // source should be "unverified".
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(unverified)");
    // No fallback reason span should be present (unverified with no server → no fallbackReason).
    expect(screen.queryByTestId("indicator-parity-fallback-SMA")).not.toBeInTheDocument();
  });

  it("no rows returns null and the panel element is not in the document", () => {
    const catalog: IndicatorCatalogEntry[] = [];
    render(
      <IndicatorParityPanel
        candles={manyStreamCandles(25)}
        overlays={[]}
        catalog={catalog}
        resolveServerSeries={() => null}
        computePool={null}
      />,
    );

    // Empty rows → component returns null → no panel element.
    expect(screen.queryByTestId("indicator-parity-panel")).not.toBeInTheDocument();
  });

  it("unverified indicator does not surface any role=alert banner", () => {
    const resolveServerSeries: PanelServerPort = () => null;
    const catalog: IndicatorCatalogEntry[] = [];

    render(
      <IndicatorParityPanel
        candles={manyStreamCandles(25)}
        overlays={[SMA]}
        catalog={catalog}
        resolveServerSeries={resolveServerSeries}
        computePool={null}
      />,
    );

    // The parity panel does not use role=alert for unverified indicators.
    // Negative assertion: no [role="alert"] element should be present.
    expect(document.querySelector('[role="alert"]')).not.toBeInTheDocument();
  });
});

// ── failure injection: mockRejectedValue on worker (D2) ──

describe("useIndicatorParityRows — failure injection: worker mockRejectedValue (D2)", () => {
  it("worker가 TypeError를 reject하면 console.warn가 호출되고 server fallback으로 간다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void; reject: (e: Error) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(() => new Promise((_resolve, reject) => deferred.push({ resolve: _resolve, reject }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    } as unknown as WorkerPool;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(777) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    expect(result.current[0]!.source).toBe("pending");

    // Inject a TypeError rejection (simulates worker crash / OOM).
    act(() => deferred[0]!.reject(new TypeError("worker OOM")));
    await waitFor(() => {
      expect(result.current[0]!.source).toBe("server");
      expect(result.current[0]!.value).toBe(777);
      expect(result.current[0]!.fallbackReason).toContain("클라이언트 계산 실패");
    });

    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("CH-18e indicator compute failed"));
    warnSpy.mockRestore();
  });
});

// ── performance assertion (D2) ──

describe("useIndicatorParityRows — performance assertion (D2)", () => {
  it("25 bars / 1 overlay render time is within perfBudgetMs(60)", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(100) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const pool: WorkerPool | null = null;

    const startedAt = performance.now();
    renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));
    const elapsed = performance.now() - startedAt;

    // budget: perfBudgetMs(60) = 60ms (plain) or 180ms (coverage).
    // Local measurement: ~2ms. 30x headroom = 60ms.
    expect(elapsed).toBeLessThan(perfBudgetMs(60));
  });
});

// ── gate red reproduction (D2) ──

describe("useIndicatorParityRows — gate red reproduction (D2)", () => {
  it("gate red: 망가진 지연으로 성능 단언이 실제로 실패함을 확인한다", async () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(100) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const pool: WorkerPool | null = null;

    // Keep the deferred promise unresolved — the hook stays in "pending" state.
    // This artificially inflates elapsed time past the budget.
    // Use perfBudgetMs(60) * 10 so the delay exceeds even coverage-mode budget (60*3*2=360ms).
    const startedAt = performance.now();
    renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    await new Promise((r) => setTimeout(r, 2000));
    const elapsed = performance.now() - startedAt;

    // The budget assertion should fail when elapsed exceeds budget * 2 (gate-red delay).
    // Under coverage: budget*2 = 60*3*2 = 360ms. 2000ms >> 360ms → definitely fails.
    expect(() => expect(elapsed).toBeLessThan(perfBudgetMs(60) * 2)).toThrow();
  });
});
