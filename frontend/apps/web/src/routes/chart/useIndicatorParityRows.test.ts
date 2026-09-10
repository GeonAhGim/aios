import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { useIndicatorParityRows, type ServerIndicatorSeriesPort } from "./useIndicatorParityRows";

// CH-18 — this hook is IndicatorParityPanel.tsx's row computation lifted out
// (task-2011 P6 split); IndicatorParityPanel.test.tsx already exercises the
// same DoD (CH-18b parity fallback, CH-18d whitelist gate, CH-18e worker
// dispatch) through the rendered component. These tests exercise the hook
// directly via renderHook so the pure-view boundary itself has coverage
// independent of that component's markup.
//
// IMPORTANT: the hook's effect deps are `[bars, overlays, catalog,
// resolveServerSeries, pool]` by reference. Every fixture below is built
// ONCE per test and captured by closure into the renderHook callback —
// never re-constructed inline inside that callback — because renderHook
// re-invokes its callback on every one of the hook's own internal
// `setResolved` re-renders; recreating array/function literals there would
// change the effect's deps on every such re-render and (since the effect
// itself calls `setResolved`) spin into an infinite render loop.

const SMA: OverlayEntry = { id: "SMA", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };
const BBANDS: OverlayEntry = {
  id: "BBANDS",
  placement: "main-overlay",
  params: ["timeperiod"],
  outputs: [{ name: "upperband", series: "line" }, { name: "middleband", series: "line" }, { name: "lowerband", series: "line" }],
  paneIndex: 0,
};

// Constant close so a real SMA(20) computation is deterministic (equals the
// close itself once the lookback window is full, at index 19) without
// mocking `computeIndicatorSeries` — matches IndicatorParityPanel.test.tsx's
// fixture convention.
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

describe("useIndicatorParityRows — CH-18d 화이트리스트 게이트(동기)", () => {
  it("BBANDS는 화이트리스트에 없으므로 즉시(effect 이전) server 소스로 폴백한 gate row를 반환한다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });
    const bars = manyBars(2);
    const overlays = [BBANDS];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toEqual([
      { id: "BBANDS", source: "server", value: 51000, fallbackReason: "클라이언트 미검증: 화이트리스트 불일치", computeNote: null },
    ]);
  });

  it("서버 참조 포트가 없으면(null 반환) BBANDS는 unverified로 fail-closed 표시된다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => null);
    const bars = manyBars(2);
    const overlays = [BBANDS];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toEqual([{ id: "BBANDS", source: "unverified", value: null, fallbackReason: null, computeNote: null }]);
  });

  // 실패주입: 서버 참조 포트가 (null 반환이 아니라) 예외를 던지는 실패 모드 —
  // buildGateRow는 이전에 이 예외를 잡지 않아 렌더 전체가 죽었다. try/catch를
  // 추가해 null-반환과 동일하게 unverified로 fail-closed 하는지 훅 레벨에서
  // 직접 확인한다(IndicatorParityPanel.test.tsx CH-18c의 컴포넌트 레벨 회귀와 쌍).
  it("서버 참조 포트가 예외를 던지면 훅 자체가 죽지 않고 BBANDS는 unverified로 fail-closed 표시된다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => {
      throw new Error("NETWORK_DOWN");
    });
    const bars = manyBars(2);
    const overlays = [BBANDS];
    const catalog = smaVerifiedCatalog();

    expect(() => renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null))).not.toThrow();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));
    expect(result.current).toEqual([{ id: "BBANDS", source: "unverified", value: null, fallbackReason: null, computeNote: null }]);
  });
});

describe("useIndicatorParityRows — CH-18e 워커 없는 환경 폴백 표기", () => {
  it("검증된 지표는 pending → client로 비동기 해소되고, pool이 null이면 computeNote가 채워진다", async () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({
      value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 100)),
    });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, null));

    expect(result.current).toEqual([{ id: "SMA", source: "pending", value: null, fallbackReason: null, computeNote: null }]);

    await waitFor(() => expect(result.current[0]!.source).toBe("client"));
    expect(result.current[0]!.value).toBe(100);
    expect(result.current[0]!.computeNote).toContain("워커 미지원");
  });

  it("주입된 WorkerPool을 통해 계산하면 computeNote 없이 client로 해소된다", async () => {
    const pool: WorkerPool = {
      submit: vi.fn().mockResolvedValue({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) }),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({
      value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)),
    });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    await waitFor(() => expect(result.current[0]!.source).toBe("client"));
    expect(result.current[0]).toEqual({ id: "SMA", source: "client", value: 50200, fallbackReason: null, computeNote: null });
    expect(pool.submit).toHaveBeenCalledTimes(1);
  });

  it("풀 오류는 서버 값으로 폴백시키고 실패 사유를 남긴다", async () => {
    const pool: WorkerPool = {
      submit: vi.fn().mockRejectedValue(new Error("WORKER_POOL_TASK_FAILED: kernel exploded")),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(77) });
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();

    const { result } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));

    await waitFor(() => expect(result.current[0]!.source).toBe("server"));
    expect(result.current[0]).toEqual({
      id: "SMA",
      source: "server",
      value: 77,
      fallbackReason: "클라이언트 계산 실패: WORKER_POOL_TASK_FAILED: kernel exploded",
      computeNote: null,
    });
  });
});

describe("useIndicatorParityRows — stale 응답/언마운트 무시", () => {
  it("입력이 바뀐 뒤 이전 요청이 늦게 도착해도 최신 결과를 덮어쓰지 않는다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(() => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const resolveServerSeries: ServerIndicatorSeriesPort = ({ bars: b }) => ({
      value: Array(b.length).fill(b.length === 25 ? 111 : 222),
    });

    const { result, rerender } = renderHook(
      ({ bars }: { bars: Bar[] }) => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool),
      { initialProps: { bars: manyBars(25) } },
    );
    expect(deferred).toHaveLength(1);

    rerender({ bars: manyBars(30) });
    expect(deferred).toHaveLength(2);

    act(() => deferred[0]!.resolve({ value: Array(25).fill(999) }));
    await Promise.resolve();
    await Promise.resolve();
    expect(result.current[0]!.value).not.toBe(999);

    act(() => deferred[1]!.resolve({ value: Array(30).fill(222) }));
    await waitFor(() => expect(result.current[0]!.value).toBe(222));
  });

  it("언마운트 후 도착한 응답은 상태를 갱신하지 않는다(경고 없음)", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn(() => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const bars = manyBars(25);
    const overlays = [SMA];
    const catalog = smaVerifiedCatalog();
    const resolveServerSeries: ServerIndicatorSeriesPort = () => ({ value: Array(25).fill(111) });

    const { unmount } = renderHook(() => useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool));
    expect(deferred).toHaveLength(1);

    unmount();
    deferred[0]!.resolve({ value: Array(25).fill(111) });
    await Promise.resolve();
    await Promise.resolve();

    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
