import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { computeIndicatorSeries } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";

// CH-18c — component-level tests (no ChartPage/IndicatorPicker mount): the
// real `GET /v1/indicators` network round trip the picker uses is not
// hermetic in this environment (a shared machine may or may not have a
// backend listening on the default base URL — see task-1968), so the
// BBANDS-not-verified scenario is exercised directly against
// IndicatorParityPanel's own props instead of through the live picker UI.

// CH-18d — spy on the real `computeIndicatorSeries`, keeping its actual
// implementation, so the tests below can assert it was never called for a
// whitelist-rejected indicator instead of only asserting the rendered
// outcome (the wiring gap this task closes is exactly "the gate exists but
// nothing calls it before compute" — a screen-only assertion would not have
// caught that).
vi.mock("@aios/chart-engine/src/compute/clientEngine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/chart-engine/src/compute/clientEngine")>();
  return { ...actual, computeIndicatorSeries: vi.fn(actual.computeIndicatorSeries) };
});

afterEach(() => {
  cleanup();
  vi.mocked(computeIndicatorSeries).mockClear();
});

const registry = createDefaultOverlayRegistry();
const BBANDS = registry.resolve("BBANDS");
const SMA = registry.resolve("SMA");

function candleAt(hourOffset: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: "50000.00",
      high: "50500.00",
      low: "49800.00",
      close: "50200.00",
      volume: "12.5",
      quote_volume: "628500.00",
    },
  };
}

function manyCandles(count: number): StreamCandle[] {
  return Array.from({ length: count }, (_, i) => candleAt(i));
}

// Catalog carrying only SMA — BBANDS is never in `VERIFIED_KERNEL_PINS` for
// ANY catalog content, so this is enough to prove BBANDS's exclusion is not
// "the catalog hasn't listed it yet" but a permanent client-side refusal.
function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [
    { name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] },
  ];
}

describe("IndicatorParityPanel — CH-18c BBANDS(영구 미검증 지표) 서버 폴백", () => {
  it("negative: BBANDS는 화이트리스트에 오를 수 없어 클라이언트 계산을 쓰지 않고, 서버 값으로 폴백하며 그 사실이 표면화된다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });

    render(
      <IndicatorParityPanel
        candles={manyCandles(2)}
        overlays={[BBANDS]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={resolveServerSeries}
      />,
    );

    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("51000.000000");
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-BBANDS")).toBeInTheDocument();
    expect(resolveServerSeries).toHaveBeenCalledWith(expect.objectContaining({ name: "BBANDS" }));
  });

  it("negative: BBANDS를 선택해도 서버 참조가 없으면(포트 미배선) 조용히 사라지지 않고 미검증으로 fail-closed 표시된다", () => {
    render(<IndicatorParityPanel candles={manyCandles(2)} overlays={[BBANDS]} catalog={smaVerifiedCatalog()} />);

    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("--");
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(unverified)");
    expect(screen.queryByTestId("indicator-parity-fallback-BBANDS")).not.toBeInTheDocument();
  });

  it("회귀 방지: SMA처럼 화이트리스트에 있는 지표는 그대로 클라이언트 계산값을 쓴다", async () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000000");
    expect(screen.queryByTestId("indicator-parity-fallback-SMA")).not.toBeInTheDocument();
  });
});

// CH-18d — verifiedIndicators.ts's whitelist gate must sit in front of
// computeIndicatorSeries in this exact component, not merely exist somewhere
// in chart-engine. Both tests below assert the compute spy's call count, not
// just the rendered outcome — reverting the `isVerifiedIndicator` pre-check
// in IndicatorParityPanel.tsx's `buildRow` makes both fail because
// computeIndicatorSeries would then run once (and throw internally) instead
// of never running at all.
describe("IndicatorParityPanel — CH-18d verifiedIndicators 실배선", () => {
  it("negative ①: verify_all.py가 검증하지 못한 지표(BBANDS)는 computeIndicatorSeries를 한 번도 호출하지 않는다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });

    render(
      <IndicatorParityPanel
        candles={manyCandles(2)}
        overlays={[BBANDS]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={resolveServerSeries}
      />,
    );

    expect(computeIndicatorSeries).not.toHaveBeenCalled();
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-BBANDS")).toBeInTheDocument();
  });

  it("negative ②: VERIFIED_KERNEL_PINS와 entry_hash가 다른(핀 드리프트) 카탈로그 항목은 computeIndicatorSeries를 호출하지 않고 서버로 폴백한다", () => {
    const pin = VERIFIED_KERNEL_PINS.SMA!;
    const driftedCatalog: IndicatorCatalogEntry[] = [
      { name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: "0".repeat(64), inputs: ["close"], outputs: ["value"] },
    ];
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => ({ value: [50200] }));

    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={driftedCatalog}
        resolveServerSeries={resolveServerSeries}
      />,
    );

    expect(computeIndicatorSeries).not.toHaveBeenCalled();
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    expect(resolveServerSeries).toHaveBeenCalledWith(expect.objectContaining({ name: "SMA" }));
  });

  it("회귀 방지: 화이트리스트를 통과하는 지표는 여전히 computeIndicatorSeries를 호출해 클라이언트 계산을 쓴다", async () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(computeIndicatorSeries).toHaveBeenCalledTimes(1);
  });
});

// CH-18e — client compute now runs through workerPool.ts (task-2039). These
// tests exercise the async dispatch, the browser's default no-Worker fallback
// note, worker-error -> server fallback, and cancellation of stale results —
// all via an injected `computePool` prop so they never depend on jsdom's lack
// of a real `Worker`/bundler `import.meta.url` worker construction.
describe("IndicatorParityPanel — CH-18e workerPool 실배선", () => {
  it("Worker 생성자가 없는 환경(jsdom)에서는 메인 스레드에서 동기 계산하되, 그 사실을 화면에 표시한다(무음 폴백 금지)", async () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(screen.getByTestId("indicator-parity-note-SMA")).toHaveTextContent("워커 미지원");
  });

  it("워커(풀) 오류는 그 지표를 서버 값으로 폴백시키고 클라이언트 값은 절대 그리지 않는다", async () => {
    const failingPool: WorkerPool = {
      submit: vi.fn().mockRejectedValue(new Error("WORKER_POOL_TASK_FAILED: kernel exploded")),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };

    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        computePool={failingPool}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 77)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)"));
    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("77.000000");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    expect(screen.queryByTestId("indicator-parity-note-SMA")).not.toBeInTheDocument();
  });

  it("입력이 바뀌어 새 계산이 시작된 뒤 이전(stale) 응답이 도착해도 화면에 반영하지 않는다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn((_task: string, _args: unknown) => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const catalog = smaVerifiedCatalog();

    const { rerender } = render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={catalog}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(25).fill(111) })}
      />,
    );
    expect(deferred).toHaveLength(1);

    rerender(
      <IndicatorParityPanel
        candles={manyCandles(30)}
        overlays={[SMA]}
        catalog={catalog}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(30).fill(222) })}
      />,
    );
    expect(deferred).toHaveLength(2);

    // Resolve the stale (first) request with a value that must never render.
    deferred[0]!.resolve({ value: Array(25).fill(999) });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText(/999/)).not.toBeInTheDocument();

    deferred[1]!.resolve({ value: Array(30).fill(222) });
    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("222.000000"));
  });

  it("언마운트 후 도착한 응답은 무시된다(setState 없음)", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn((_task: string, _args: unknown) => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(25).fill(111) })}
      />,
    );
    expect(deferred).toHaveLength(1);

    unmount();
    deferred[0]!.resolve({ value: Array(25).fill(111) });
    await Promise.resolve();
    await Promise.resolve();

    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
