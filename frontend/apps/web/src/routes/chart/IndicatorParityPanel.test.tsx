import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { CLIENT_ENGINE_COMPUTE_TASK, computeIndicatorSeries, type Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";
import { createWorkerPool, type WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { resolveVerifiedIndicators } from "@aios/chart-engine/src/compute/verifiedIndicators";

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

  // 실패주입: resolveServerSeries는 IND-12 카탈로그와 달리 이 leaf가 배선하는 실제
  // 서버 왕복 포트가 없어(모듈 docstring 참고) 부모(ChartPage)가 주입하는 콜백이다 —
  // 네트워크 단절/파싱 실패 등으로 그 콜백이 예외를 던지는 것은 null을 반환하는 것과
  // 마찬가지로 실제로 일어날 수 있는 실패 모드다. 이전에는 buildGateRow가 이 예외를
  // 잡지 않아 IndicatorParityPanel 전체 렌더가 죽었다(다른 패널의 지표까지 함께
  // 사라짐) — useIndicatorParityRows.ts의 buildGateRow에 try/catch를 추가해
  // null-반환 케이스와 동일하게 unverified로 fail-closed 하도록 고쳤다.
  it("negative ③(실패주입): 서버 참조 포트가 예외를 던져도 렌더가 죽지 않고 BBANDS는 unverified로 fail-closed 표시된다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => {
      throw new Error("NETWORK_DOWN: indicator reference fetch failed");
    });

    expect(() =>
      render(
        <IndicatorParityPanel
          candles={manyCandles(2)}
          overlays={[BBANDS]}
          catalog={smaVerifiedCatalog()}
          resolveServerSeries={resolveServerSeries}
        />,
      ),
    ).not.toThrow();

    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("--");
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(unverified)");
    expect(screen.queryByTestId("indicator-parity-fallback-BBANDS")).not.toBeInTheDocument();
  });

  // 수치 성능: CH-18c 게이트 경로(candles→bars 변환 + 미검증 지표의 동기 서버-폴백
  // 조회)는 렌더 중(useEffect 이전) 동기로 도는 유일한 구간이라, 큰 캔들 창에서도
  // 메인 스레드를 눈에 띄게 막지 않아야 한다 — ChartPage가 실제로 그리는 수만 개
  // 캔들 규모에서 고정 ms 예산을 단언한다(CH-19a/b와 동일한 수치 성능 축).
  it("수치 성능: 캔들 3만 개 + BBANDS 미검증 게이트 경로가 3000ms 예산 내로 렌더된다", () => {
    const bigCandles = manyCandles(30_000);
    const resolveServerSeries: ServerIndicatorSeriesPort = ({ name }) => {
      if (name !== "BBANDS") return null;
      const series = Array.from({ length: 30_000 }, (_, i) => (i < 19 ? null : 50000 + i));
      return { upperband: series, middleband: series, lowerband: series };
    };

    const start = performance.now();
    render(
      <IndicatorParityPanel
        candles={bigCandles}
        overlays={[BBANDS]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={resolveServerSeries}
      />,
    );
    const elapsedMs = performance.now() - start;

    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
    // 3s: generous margin for a contended CI host running many suites in parallel
    // (isolated single-file run measures ~50-60ms; full-suite parallel run measured
    // ~550ms) while still catching a real O(n^2) or worse regression in this path.
    expect(elapsedMs).toBeLessThan(3000);
  });

  // 게이트 적색 재현: cc5ec7c5 이전에는 IndicatorParityPanel이 overlays를
  // `resolveVerifiedIndicators` 화이트리스트로 먼저 필터링한 뒤에만 행을 만들었다 —
  // BBANDS는 그 필터를 통과하지 못해 서버 폴백을 시도조차 하지 않고 행 자체가
  // 조용히 사라졌다(커밋 메시지 "행 자체가 렌더되지 않음"). 여기서는 그 되돌린
  // 동작을 실제 `resolveVerifiedIndicators`(여전히 export됨, 가짜 재구현 아님)로
  // 정확히 재현해 — 동일 픽스처에서 사전 필터링된 props는 BBANDS 행이 전혀 없고
  // (적색), 바로 다음 줄의 실제 컴포넌트(전체 overlays)는 폴백 배지와 함께 행을
  // 렌더한다(녹색) — git revert 없이 파일 내에서 자동으로 대조한다.
  it("게이트 적색 재현: cc5ec7c5 이전 사전 필터를 재현하면 BBANDS 행이 아예 없다(적색) vs 실제 컴포넌트는 폴백 표시된다(녹색)", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });
    const catalog = smaVerifiedCatalog();

    // 적색: pre-cc5ec7c5 필터를 실제 whitelist 함수로 재현 — BBANDS는 whitelist에
    // 절대 오르지 못하므로 overlays가 빈 배열이 되고, 패널은 아무것도 렌더하지 않는다.
    const verified = resolveVerifiedIndicators(catalog);
    const preFilteredOverlays = [BBANDS].filter((overlay) => verified.has(overlay.id));
    expect(preFilteredOverlays).toHaveLength(0);
    const { container: redContainer } = render(
      <IndicatorParityPanel candles={manyCandles(2)} overlays={preFilteredOverlays} catalog={catalog} resolveServerSeries={resolveServerSeries} />,
    );
    expect(redContainer.querySelector('[data-testid="indicator-parity-panel"]')).not.toBeInTheDocument();
    expect(screen.queryByTestId("indicator-parity-value-BBANDS")).not.toBeInTheDocument();

    // 녹색: 실제 컴포넌트는 사전 필터 없이 모든 selected overlay에 행을 준다.
    render(
      <IndicatorParityPanel candles={manyCandles(2)} overlays={[BBANDS]} catalog={catalog} resolveServerSeries={resolveServerSeries} />,
    );
    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("51000.000000");
    expect(screen.getByTestId("indicator-parity-fallback-BBANDS")).toBeInTheDocument();
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

// DEEPEN 2039 (docs/audit/DEPTH_CH.md): the CH-18e leaf's own workerPool.test.ts
// gaps (수치 성능 단언 없음, 게이트 적색 재현 없음) also show up one layer up, at
// this panel's wiring — nothing here ever measured that dispatching through the
// pool actually beats synchronous main-thread compute in wall-clock ms, and
// nothing reproduced the pre-2039 "동기 계산" state to prove the async dispatch
// is load-bearing rather than incidental.

const PERF_PARAMS: Readonly<Record<string, Record<string, number>>> = {
  SMA: { timeperiod: 20 },
  EMA: { timeperiod: 20 },
  RSI: { timeperiod: 14 },
  ATR: { timeperiod: 14 },
  CCI: { timeperiod: 14 },
  WILLR: { timeperiod: 14 },
  MFI: { timeperiod: 14 },
  MACD: { fastperiod: 12, slowperiod: 26, signalperiod: 9 },
  STOCH: { fastk_period: 5, slowk_period: 3, slowd_period: 3 },
  OBV: {},
};

const PERF_SPEC_SHAPES: Readonly<Record<string, { inputs: readonly string[]; outputs: readonly string[] }>> = {
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

function allVerifiedCatalog(): IndicatorCatalogEntry[] {
  return Object.entries(VERIFIED_KERNEL_PINS).map(([name, pin]) => ({
    name,
    tier: pin.tier,
    category: "test",
    version: "ind-v1",
    hash: pin.entryHash,
    inputs: PERF_SPEC_SHAPES[name]!.inputs,
    outputs: PERF_SPEC_SHAPES[name]!.outputs,
  }));
}

/** Fake `WorkerPool` backed by real `createWorkerPool` busy-tracking, running the *actual* `computeIndicatorSeries` after an artificial per-task delay — a stand-in for a real Worker's message round trip without needing jsdom `Worker` support. */
function createSimulatedComputePool(backendCount: number, perTaskDelayMs: number): WorkerPool {
  const backends = Array.from({ length: backendCount }, () => ({
    async run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
      if (perTaskDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, perTaskDelayMs));
      if (task !== CLIENT_ENGINE_COMPUTE_TASK) throw new Error(`unexpected task ${task}`);
      return computeIndicatorSeries(args as Parameters<typeof computeIndicatorSeries>[0]) as unknown as TResult;
    },
    dispose: vi.fn(),
  }));
  return createWorkerPool(backends);
}

describe("IndicatorParityPanel — DEEPEN 2039 numeric performance & gate red reproduction", () => {
  const perfNames = ["SMA", "EMA", "RSI", "ATR", "CCI"] as const;
  const perfOverlays = perfNames.map((name) => registry.resolve(name));
  const perfCatalog = allVerifiedCatalog().filter((entry) => (perfNames as readonly string[]).includes(entry.name));

  it("수치 성능: 4-백엔드 풀로 5개 검증 지표를 계산하면, 동일 부하를 1-백엔드(직렬) 풀로 돌릴 때보다 렌더 완료까지의 실측 ms가 뚜렷이 짧다", async () => {
    const perTaskDelayMs = 40;

    async function measure(pool: WorkerPool): Promise<number> {
      const start = performance.now();
      const { unmount } = render(
        <IndicatorParityPanel
          candles={manyCandles(30)}
          overlays={perfOverlays}
          catalog={perfCatalog}
          computePool={pool}
          resolveServerSeries={() => null}
        />,
      );
      // resolveServerSeries returns null -> resolveIndicatorSeries fails closed to
      // "unverified" once the pool settles; only the *timing* of that transition
      // (not which source string it lands on) is what this test cares about.
      await waitFor(
        () => {
          for (const overlay of perfOverlays) {
            expect(screen.getByTestId(`indicator-parity-source-${overlay.id}`)).toHaveTextContent("(unverified)");
          }
        },
        { timeout: 5000 },
      );
      const elapsed = performance.now() - start;
      unmount();
      pool.dispose();
      return elapsed;
    }

    const serialElapsed = await measure(createSimulatedComputePool(1, perTaskDelayMs));
    const concurrentElapsed = await measure(createSimulatedComputePool(4, perTaskDelayMs));

    // Serial lower bound: 5 * 40ms = 200ms. Generous CI margin, but a regression
    // back to one shared backend (or to no pool at all) would fail this.
    expect(serialElapsed).toBeGreaterThanOrEqual(perfNames.length * perTaskDelayMs * 0.8);
    expect(concurrentElapsed).toBeLessThan(serialElapsed);
  }, 10000);

  it("게이트 적색 재현: pre-2039 방식(계산을 렌더 경로 안에서 동기로 끝냄)을 재현하면 그 호출 자체가 계산 완료까지 블로킹된다(적색); 실제 컴포넌트는 풀에 위임해 render() 호출이 계산을 기다리지 않고 즉시 반환된다(녹색)", () => {
    const heavyCandles = manyCandles(20_000);
    const heavyBars: Bar[] = heavyCandles.map((candle) => ({
      open: Number(candle.record.open),
      high: Number(candle.record.high),
      low: Number(candle.record.low),
      close: Number(candle.record.close),
      volume: Number(candle.record.volume),
    }));
    const allOverlays = Object.keys(VERIFIED_KERNEL_PINS).map((name) => registry.resolve(name));
    const catalog = allVerifiedCatalog();

    // 적색: 17ef81ee 이전에는 client compute가 workerPool을 거치지 않고 렌더 경로
    // 안에서 computeIndicatorSeries를 직접, 동기로 호출했다(모듈 docstring
    // "현재 메인 스레드 동기 계산" 참고) — 그 호출 자체가 완료될 때까지 블로킹된다.
    const redStart = performance.now();
    for (const overlay of allOverlays) {
      computeIndicatorSeries({ name: overlay.id, params: PERF_PARAMS[overlay.id] ?? {}, bars: heavyBars, catalog });
    }
    const redElapsed = performance.now() - redStart;

    // 녹색: 실제 컴포넌트는 동일 크기의 부하를 풀에 위임한다(지연 0 — 동기 블로킹
    // 여부만 비교, 왕복 지연은 위 수치 성능 테스트가 이미 담당). render() 자체는
    // useEffect 안에서만 pool.submit을 호출하므로 계산 완료를 기다리지 않는다.
    const pool = createSimulatedComputePool(allOverlays.length, 0);
    const greenStart = performance.now();
    render(
      <IndicatorParityPanel candles={heavyCandles} overlays={allOverlays} catalog={catalog} computePool={pool} resolveServerSeries={() => null} />,
    );
    const greenElapsed = performance.now() - greenStart;
    pool.dispose();

    expect(redElapsed).toBeGreaterThan(5); // sanity: the red mutant really did block on real work
    expect(greenElapsed).toBeLessThan(redElapsed * 0.5);
  });
});
