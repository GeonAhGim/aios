import type { DemoOhlcBar } from "./demoDataset";

// task-2632(U-10): 데모 모드 전용 초경량 백테스트 — 실제 BacktestPanel.tsx가
// 쓰는 서버 /v1/backtests/quick 엔진과는 별개다(그 엔진은 실 계좌·주문 모델과
// 얽혀 있어 데모 목적의 순수 클라이언트 계산에는 과하다). 여기서는 SMA(5/20)
// 교차만으로 "5분 안에 첫 백테스트"를 보여주는 게 목적이라 결정론적 순수
// 함수 하나로 충분하다 — 새 실행 엔진을 프로덕션 경로에 얹지 않는다.
export const DEMO_BACKTEST_FAST_PERIOD = 5;
export const DEMO_BACKTEST_SLOW_PERIOD = 20;
export const DEMO_BACKTEST_INITIAL_EQUITY = 10_000;

export interface DemoBacktestResult {
  initialEquity: number;
  finalEquity: number;
  trades: number;
  maxDrawdownPct: number;
  bars: number;
}

function sma(closes: number[], period: number, index: number): number | null {
  if (index + 1 < period) return null;
  let sum = 0;
  for (let i = index - period + 1; i <= index; i += 1) sum += closes[i];
  return sum / period;
}

/** SMA(5) x SMA(20) 골든/데드 크로스 전략을 일봉 종가에 대해 순차 시뮬레이션한다. */
export function runDemoBacktest(bars: readonly DemoOhlcBar[]): DemoBacktestResult {
  if (bars.length === 0) {
    return {
      initialEquity: DEMO_BACKTEST_INITIAL_EQUITY,
      finalEquity: DEMO_BACKTEST_INITIAL_EQUITY,
      trades: 0,
      maxDrawdownPct: 0,
      bars: 0,
    };
  }

  const closes = bars.map((bar) => bar.close);
  let cash = DEMO_BACKTEST_INITIAL_EQUITY;
  let position = 0;
  let trades = 0;
  let peakEquity = DEMO_BACKTEST_INITIAL_EQUITY;
  let maxDrawdownPct = 0;
  let prevFast: number | null = null;
  let prevSlow: number | null = null;

  for (let i = 0; i < closes.length; i += 1) {
    const fast = sma(closes, DEMO_BACKTEST_FAST_PERIOD, i);
    const slow = sma(closes, DEMO_BACKTEST_SLOW_PERIOD, i);

    if (fast !== null && slow !== null && prevFast !== null && prevSlow !== null) {
      const crossedUp = prevFast <= prevSlow && fast > slow;
      const crossedDown = prevFast >= prevSlow && fast < slow;
      if (crossedUp && position === 0) {
        position = cash / closes[i];
        cash = 0;
        trades += 1;
      } else if (crossedDown && position > 0) {
        cash = position * closes[i];
        position = 0;
        trades += 1;
      }
    }

    prevFast = fast;
    prevSlow = slow;

    const equity = cash + position * closes[i];
    if (equity > peakEquity) peakEquity = equity;
    const drawdownPct = peakEquity > 0 ? ((peakEquity - equity) / peakEquity) * 100 : 0;
    if (drawdownPct > maxDrawdownPct) maxDrawdownPct = drawdownPct;
  }

  const finalEquity = cash + position * closes[closes.length - 1];
  return {
    initialEquity: DEMO_BACKTEST_INITIAL_EQUITY,
    finalEquity,
    trades,
    maxDrawdownPct,
    bars: bars.length,
  };
}
