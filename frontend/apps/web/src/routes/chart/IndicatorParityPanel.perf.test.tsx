import "../../i18n";
import "@testing-library/jest-dom/vitest";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { CLIENT_ENGINE_COMPUTE_TASK, computeIndicatorSeries, type Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";
import { createWorkerPool, type WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { registry, manyCandles, lastDefined } from "./IndicatorParityPanel.fixtures";

afterEach(() => {
  cleanup();
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

  // manyCandles(N) generates every candle with the same OHLCV
  // (candleAt only varies openTimeMs) — SMA(20) of a constant 50200 close
  // series is therefore exactly 50200, a value this test can assert on
  // without reimplementing SMA.
  const SMA_EXPECTED_VALUE = "50200.000000";

  it("수치 성능: 4-백엔드 풀로 5개 검증 지표를 계산하면, 동일 부하를 1-백엔드(직렬) 풀로 돌릴 때보다 렌더 완료까지의 실측 ms가 뚜렷이 짧고, 풀을 거쳐 돌아온 실제 계산값도 손상 없이 그대로 반영된다", async () => {
    const perTaskDelayMs = 40;
    // Only SMA gets a matching server reference (50200) so its round trip
    // resolves to "(client)" with a value this test can check for
    // correctness; the other four keep resolveServerSeries === null and stay
    // "(unverified)" (fail-closed, per parityCheck.ts) exactly as before —
    // this isolates "did the pool relay the right number" from the
    // deliberately-untouched fail-closed default the rest of this suite
    // already covers.
    // SMA(20)'s first 19 outputs are null (lookback not met yet) — the
    // reference must carry the same nulls in the same positions, or
    // `checkIndicatorParity` reads a null/number disagreement as an
    // Infinite-error mismatch and falls back to "(server)" instead of
    // confirming "(client)" (parityCheck.ts's own null-must-match-null rule).
    const smaReference = Array.from({ length: 30 }, (_, i) => (i < 19 ? null : 50200));
    const resolveServerSeries: ServerIndicatorSeriesPort = ({ name }) => (name === "SMA" ? { value: smaReference } : null);

    async function measure(pool: WorkerPool): Promise<number> {
      const start = performance.now();
      const { unmount } = render(
        <IndicatorParityPanel
          candles={manyCandles(30)}
          overlays={perfOverlays}
          catalog={perfCatalog}
          computePool={pool}
          resolveServerSeries={resolveServerSeries}
        />,
      );
      // Timing under test: how long until every row has settled out of
      // "pending" — SMA to "(client)" (it has a matching server reference),
      // the rest to "(unverified)" (fail-closed, no server reference).
      await waitFor(
        () => {
          expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)");
          for (const overlay of perfOverlays) {
            if (overlay.id === "SMA") continue;
            expect(screen.getByTestId(`indicator-parity-source-${overlay.id}`)).toHaveTextContent("(unverified)");
          }
        },
        { timeout: 5000 },
      );
      const elapsed = performance.now() - start;
      // Data-value check (not just UI state/labels): the value rendered for
      // SMA came from `computeIndicatorSeries` running inside the simulated
      // worker backend, round-tripped through `createWorkerPool.submit` and
      // `useIndicatorParityRows`'s `.then(client => ...)` handler. Asserting
      // the exact expected number here — not merely that the source label
      // flipped to "(client)" — is what proves that payload survived the
      // pool round trip intact rather than the panel merely reacting to a
      // resolved promise of unknown content.
      expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent(SMA_EXPECTED_VALUE);
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

  it("게이트 적색 재현: pre-2039 방식(계산을 렌더 경로 안에서 동기로 끝냄)을 재현하면 그 호출 자체가 계산 완료까지 블로킹된다(적색); 실제 컴포넌트는 풀에 위임해 render() 호출이 계산을 기다리지 않고 즉시 반환되며(녹색), 그 뒤에 실제로 도착하는 값도 동기 경로가 계산했던 값과 동일하다", async () => {
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
    // 이 동일 호출의 SMA 출력을 아래 "녹색" 경로가 실제로 화면에 그리는 값과
    // 대조할 기준값으로도 재사용한다 — 재구현이 아니라 같은 real computeIndicatorSeries다.
    const redStart = performance.now();
    let redSmaSeries: ReadonlyArray<number | null> | null = null;
    for (const overlay of allOverlays) {
      const result = computeIndicatorSeries({ name: overlay.id, params: PERF_PARAMS[overlay.id] ?? {}, bars: heavyBars, catalog });
      if (overlay.id === "SMA") redSmaSeries = result.value ?? [];
    }
    const redElapsed = performance.now() - redStart;
    expect(redSmaSeries).not.toBeNull();
    const redSmaValue = lastDefined(redSmaSeries!);
    expect(redSmaValue).not.toBeNull();

    // 녹색: 실제 컴포넌트는 동일 크기의 부하를 풀에 위임한다(지연 0 — 동기 블로킹
    // 여부만 비교, 왕복 지연은 위 수치 성능 테스트가 이미 담당). render() 자체는
    // useEffect 안에서만 pool.submit을 호출하므로 계산 완료를 기다리지 않는다.
    // resolveServerSeries가 SMA에 한해 위 적색 경로와 완전히 같은 시리즈(동일
    // 배열, index별 null 위치까지 그대로)를 참조로 주므로 checkIndicatorParity가
    // 오차 없음(ok)으로 판정해 "(client)"로 확정되고, 화면에 최종 반영되는 값이
    // render() 호출 시점에 존재하지 않던 "아무 값"이 아니라 동기 경로가 계산한
    // 것과 같은 실제 데이터임을 검증할 수 있다.
    const pool = createSimulatedComputePool(allOverlays.length, 0);
    const resolveServerSeries: ServerIndicatorSeriesPort = ({ name }) => (name === "SMA" ? { value: redSmaSeries! } : null);
    const greenStart = performance.now();
    render(
      <IndicatorParityPanel candles={heavyCandles} overlays={allOverlays} catalog={catalog} computePool={pool} resolveServerSeries={resolveServerSeries} />,
    );
    const greenElapsed = performance.now() - greenStart;

    expect(redElapsed).toBeGreaterThan(5); // sanity: the red mutant really did block on real work
    expect(greenElapsed).toBeLessThan(redElapsed * 0.5);

    // Data-value check (not just render()'s return timing): the pool
    // eventually delivers the exact same SMA value the blocking red path
    // computed — proving async dispatch changed *when* the main thread is
    // freed, not *what* value ends up on screen.
    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent(redSmaValue!.toFixed(6));
    pool.dispose();
  });
});
