/**
 * CH-19b — deterministic synthetic OHLCV generator and percentile helper,
 * split out of density_bench.mjs to keep that file under the 300-line
 * budget. Same shape as packages/ui-web/scripts/bench_chart_candles.mjs's
 * CH-0 generator (seeded PRNG, fixed epoch, no Date.now/Math.random) so the
 * two benches stay methodologically comparable.
 */

function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function genCandles(n, seed = 42) {
  const rnd = mulberry32(seed);
  const candles = new Array(n);
  let price = 100;
  const startTime = 1_700_000_000;
  for (let i = 0; i < n; i++) {
    const drift = (rnd() - 0.5) * 0.6;
    const open = price;
    const close = Math.max(0.01, open + drift);
    const high = Math.max(open, close) + rnd() * 0.4;
    const low = Math.min(open, close) - rnd() * 0.4;
    const volume = 10 + rnd() * 500;
    candles[i] = { time: startTime + i * 60, open, high, low, close, volume };
    price = close;
  }
  return candles;
}

export function percentile(values, p) {
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return Math.round(sorted[idx] * 1000) / 1000;
}
