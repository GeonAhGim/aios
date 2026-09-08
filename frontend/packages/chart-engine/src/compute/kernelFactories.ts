/**
 * CH-18a kernel factories — the 10 IND-7g-verified indicator kernels
 * (`KERNEL_FACTORIES`), each a hand-port of the backend IND-1 streaming
 * kernel state machine. Split out of `clientEngine.ts` (task-2098, P6
 * 300-line cap) with no behavior change.
 *
 * Must stay name-for-name in sync with `verifiedIndicators.ts`
 * `VERIFIED_KERNEL_PINS` — enforced by a test.
 */

import { Ema, type Bar, type KernelFactory, Wilder, Window, requirePeriod } from "./kernelPrimitives";

const createSma: KernelFactory = (params, name) => {
  const w = new Window(requirePeriod(params, "timeperiod", name), name);
  return {
    update: (bar) => (w.push(bar.close!) ? [w.mean()] : null),
  };
};

const createEma: KernelFactory = (params, name) => {
  const ema = new Ema(requirePeriod(params, "timeperiod", name), name);
  return {
    update: (bar) => {
      const v = ema.push(bar.close!);
      return v === null ? null : [v];
    },
  };
};

const createRsi: KernelFactory = (params, name) => {
  const period = requirePeriod(params, "timeperiod", name);
  const gain = new Wilder(period, name);
  const loss = new Wilder(period, name);
  let prev: number | null = null;
  return {
    update: (bar) => {
      const close = bar.close!;
      if (prev === null) {
        prev = close;
        return null;
      }
      const diff = close - prev;
      prev = close;
      const gainV = gain.push(diff > 0 ? diff : 0.0);
      const lossV = loss.push(diff < 0 ? -diff : 0.0);
      if (gainV === null || lossV === null) return null;
      const total = gainV + lossV;
      return [total !== 0.0 ? 100.0 * (gainV / total) : 0.0];
    },
  };
};

const createAtr: KernelFactory = (params, name) => {
  const wilder = new Wilder(requirePeriod(params, "timeperiod", name), name);
  let prevClose: number | null = null;
  return {
    update: (bar) => {
      const { high, low, close } = bar as Required<Pick<Bar, "high" | "low" | "close">>;
      if (prevClose === null) {
        prevClose = close;
        return null;
      }
      const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
      prevClose = close;
      const v = wilder.push(tr);
      return v === null ? null : [v];
    },
  };
};

const createCci: KernelFactory = (params, name) => {
  const w = new Window(requirePeriod(params, "timeperiod", name), name);
  return {
    update: (bar) => {
      const { high, low, close } = bar as Required<Pick<Bar, "high" | "low" | "close">>;
      const tp = (high + low + close) / 3.0;
      if (!w.push(tp)) return null;
      const avg = w.mean();
      const md = w.meanAbsDev(avg);
      const num = tp - avg;
      return [num !== 0.0 && md !== 0.0 ? num / (0.015 * md) : 0.0];
    },
  };
};

const createWillr: KernelFactory = (params, name) => {
  const period = requirePeriod(params, "timeperiod", name);
  const hi = new Window(period, name);
  const lo = new Window(period, name);
  return {
    update: (bar) => {
      const { high, low, close } = bar as Required<Pick<Bar, "high" | "low" | "close">>;
      hi.push(high);
      if (!lo.push(low)) return null;
      const hh = hi.max();
      const ll = lo.min();
      const diff = hh - ll;
      return [diff !== 0.0 ? ((hh - close) / diff) * -100.0 : 0.0];
    },
  };
};

const createMfi: KernelFactory = (params, name) => {
  const period = requirePeriod(params, "timeperiod", name);
  const pos = new Window(period, name);
  const neg = new Window(period, name);
  let prevTp: number | null = null;
  return {
    update: (bar) => {
      const { high, low, close, volume } = bar as Required<
        Pick<Bar, "high" | "low" | "close" | "volume">
      >;
      const tp = (high + low + close) / 3.0;
      if (prevTp === null) {
        prevTp = tp;
        return null;
      }
      const diff = tp - prevTp;
      prevTp = tp;
      const flow = tp * volume;
      pos.push(diff > 0 ? flow : 0.0);
      if (!neg.push(diff < 0 ? flow : 0.0)) return null;
      const posTotal = pos.total();
      const total = posTotal + neg.total();
      return [total < 1.0 ? 0.0 : 100.0 * (posTotal / total)];
    },
  };
};

const createMacd: KernelFactory = (params, name) => {
  const fastPeriod = requirePeriod(params, "fastperiod", name);
  const slowPeriod = requirePeriod(params, "slowperiod", name);
  const signalPeriod = requirePeriod(params, "signalperiod", name);
  const fast = new Ema(fastPeriod, name, slowPeriod - fastPeriod);
  const slow = new Ema(slowPeriod, name);
  const signal = new Ema(signalPeriod, name);
  return {
    update: (bar) => {
      const close = bar.close!;
      const fastV = fast.push(close);
      const slowV = slow.push(close);
      if (fastV === null || slowV === null) return null;
      const macd = fastV - slowV;
      const signalV = signal.push(macd);
      return signalV === null ? null : [macd, signalV, macd - signalV];
    },
  };
};

const createStoch: KernelFactory = (params, name) => {
  const fastkPeriod = requirePeriod(params, "fastk_period", name);
  const slowkPeriod = requirePeriod(params, "slowk_period", name);
  const slowdPeriod = requirePeriod(params, "slowd_period", name);
  const hi = new Window(fastkPeriod, name);
  const lo = new Window(fastkPeriod, name);
  const kWindow = new Window(slowkPeriod, name);
  const dWindow = new Window(slowdPeriod, name);
  return {
    update: (bar) => {
      const { high, low, close } = bar as Required<Pick<Bar, "high" | "low" | "close">>;
      hi.push(high);
      if (!lo.push(low)) return null;
      const hh = hi.max();
      const ll = lo.min();
      const diff = hh - ll;
      const fastk = diff !== 0.0 ? ((close - ll) / diff) * 100.0 : 0.0;
      if (!kWindow.push(fastk)) return null;
      const slowk = kWindow.mean();
      return dWindow.push(slowk) ? [slowk, dWindow.mean()] : null;
    },
  };
};

const createObv: KernelFactory = () => {
  let prevClose: number | null = null;
  let obv = 0.0;
  return {
    update: (bar) => {
      const { close, volume } = bar as Required<Pick<Bar, "close" | "volume">>;
      if (prevClose === null) obv = volume;
      else if (close > prevClose) obv = obv + volume;
      else if (close < prevClose) obv = obv + -volume;
      prevClose = close;
      return [obv];
    },
  };
};

/** Must stay name-for-name in sync with `verifiedIndicators.ts` `VERIFIED_KERNEL_PINS` — enforced by a test. */
export const KERNEL_FACTORIES: Readonly<Record<string, KernelFactory>> = Object.freeze({
  SMA: createSma,
  EMA: createEma,
  RSI: createRsi,
  ATR: createAtr,
  CCI: createCci,
  WILLR: createWillr,
  MFI: createMfi,
  MACD: createMacd,
  STOCH: createStoch,
  OBV: createObv,
});
