// task-2632(U-10): 데모 모드는 실 연결·실 시세 없이 "고정 샘플 데이터셋(1년 M1
// 3종목)"만으로 모든 화면이 동작해야 한다(spec). 실제로 525,600개(365일×1440분)
// 분봉을 저장하는 대신, 종목 id로 시드를 고정한 결정론적 PRNG(mulberry32)로 그때
// 그때 계산한다 — Math.random을 쓰면 렌더/테스트마다 값이 달라져 "고정 샘플"이
// 아니게 되고, 실제 파일에 52만 행을 박아두면 diff/리뷰가 불가능해진다. 화면에는
// 1분봉을 그대로 그리지 않고 일봉으로 집계해 반환한다(365개 캔들, 렌더 비용을
// 억제 — lightweight-charts에 52만 포인트를 넘기지 않는다).
export interface DemoInstrument {
  id: string;
  label: string;
}

export const DEMO_INSTRUMENTS: readonly DemoInstrument[] = [
  { id: "DEMO-BTCUSDT", label: "BTC/USDT" },
  { id: "DEMO-ETHUSDT", label: "ETH/USDT" },
  { id: "DEMO-SOLUSDT", label: "SOL/USDT" },
];

export const DEMO_DATASET_DAYS = 365;
export const DEMO_MINUTES_PER_DAY = 24 * 60;
// 데모 데이터셋은 "지금"이 아니라 고정된 시각을 원점으로 삼는다 — Date.now()를
// 쓰면 테스트/렌더 시점마다 캔들 타임스탬프가 달라져 스냅샷/e2e가 불안정해진다.
export const DEMO_DATASET_START_MS = Date.UTC(2025, 0, 1, 0, 0, 0);

export interface DemoOhlcBar {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

function seedFor(instrumentId: string): number {
  let hash = 0;
  for (let i = 0; i < instrumentId.length; i += 1) {
    hash = (hash * 31 + instrumentId.charCodeAt(i)) >>> 0;
  }
  return hash || 1;
}

// mulberry32: 시드 하나로 재현 가능한 [0,1) 난수열을 만드는 표준 소형 PRNG.
function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function findDemoInstrument(instrumentId: string): DemoInstrument | undefined {
  return DEMO_INSTRUMENTS.find((instrument) => instrument.id === instrumentId);
}

const dailyCandleCache = new Map<string, DemoOhlcBar[]>();

/** 1년치 결정론적 M1 워크를 일봉으로 집계해 반환한다. 알 수 없는 종목이면 빈 배열. */
export function getDemoDailyCandles(instrumentId: string): DemoOhlcBar[] {
  const cached = dailyCandleCache.get(instrumentId);
  if (cached) return cached;
  if (!findDemoInstrument(instrumentId)) return [];

  const seed = seedFor(instrumentId);
  const rng = mulberry32(seed);
  const basePrice = 100 + (seed % 900);
  const stepScale = basePrice * 0.0015;
  let price = basePrice;
  const startSec = Math.floor(DEMO_DATASET_START_MS / 1000);
  const days: DemoOhlcBar[] = [];

  for (let day = 0; day < DEMO_DATASET_DAYS; day += 1) {
    const open = price;
    let high = price;
    let low = price;
    for (let minute = 0; minute < DEMO_MINUTES_PER_DAY; minute += 1) {
      const drift = (rng() - 0.5) * stepScale;
      price = Math.max(1, price + drift);
      if (price > high) high = price;
      if (price < low) low = price;
    }
    days.push({ time: startSec + day * 86400, open, high, low, close: price });
  }

  dailyCandleCache.set(instrumentId, days);
  return days;
}
