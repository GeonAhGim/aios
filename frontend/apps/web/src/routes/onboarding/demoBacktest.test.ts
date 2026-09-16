import { describe, expect, it } from "vitest";
import type { DemoOhlcBar } from "./demoDataset";
import { DEMO_BACKTEST_INITIAL_EQUITY, runDemoBacktest } from "./demoBacktest";

function bar(day: number, close: number): DemoOhlcBar {
  return { time: day * 86400, open: close, high: close, low: close, close };
}

describe("runDemoBacktest", () => {
  it("negative: 빈 캔들 배열이면 거래 없이 초기 자산을 그대로 돌려준다", () => {
    const result = runDemoBacktest([]);
    expect(result).toEqual({
      initialEquity: DEMO_BACKTEST_INITIAL_EQUITY,
      finalEquity: DEMO_BACKTEST_INITIAL_EQUITY,
      trades: 0,
      maxDrawdownPct: 0,
      bars: 0,
    });
  });

  it("횡보 후 급등하는 시장에서는 골든크로스 이후 매수해 최종 자산이 초기값보다 커진다", () => {
    // 처음 20일은 SMA5=SMA20=100으로 평평하게 유지해 두 이평선을 정렬시킨 뒤,
    // 이후 40일은 가파르게 올려 SMA5가 SMA20을 위로 뚫는 골든크로스를 만든다
    // (직선 단조 상승이면 SMA5가 처음부터 SMA20보다 위라 교차 자체가 없다).
    const flat = Array.from({ length: 20 }, (_, i) => bar(i, 100));
    const rally = Array.from({ length: 40 }, (_, i) => bar(20 + i, 100 + i * 3));
    const bars = [...flat, ...rally];
    const result = runDemoBacktest(bars);
    expect(result.bars).toBe(60);
    expect(result.trades).toBeGreaterThan(0);
    expect(result.finalEquity).toBeGreaterThan(DEMO_BACKTEST_INITIAL_EQUITY);
  });

  it("negative: 평평한 시장(교차 없음)에서는 거래가 발생하지 않고 자산이 보존된다", () => {
    const bars = Array.from({ length: 60 }, (_, i) => bar(i, 100));
    const result = runDemoBacktest(bars);
    expect(result.trades).toBe(0);
    expect(result.finalEquity).toBe(DEMO_BACKTEST_INITIAL_EQUITY);
    expect(result.maxDrawdownPct).toBe(0);
  });

  it("같은 입력에 대해 항상 같은 결과를 반환한다(결정론적 재현성)", () => {
    const bars = Array.from({ length: 40 }, (_, i) => bar(i, 100 + Math.sin(i) * 10));
    expect(runDemoBacktest(bars)).toEqual(runDemoBacktest(bars));
  });

  it("perf: 365개 일봉 시뮬레이션이 20ms 이내에 끝난다", () => {
    const bars = Array.from({ length: 365 }, (_, i) => bar(i, 100 + Math.sin(i / 5) * 15));
    const started = performance.now();
    runDemoBacktest(bars);
    const elapsedMs = performance.now() - started;
    expect(elapsedMs).toBeLessThan(20);
  });
});
