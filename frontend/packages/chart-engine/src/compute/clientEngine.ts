/**
 * CH-18a — client-side incremental indicator engine: a bar-by-bar TS port of
 * the backend IND-1 streaming kernel (`src/core/indicators/engine/incremental.py`,
 * task-1738 IND-7g reference vectors), restricted to the whitelist gate in
 * `verifiedIndicators.ts`.
 *
 * Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11
 * CH-18 (compute path + whitelist gate only — actual server/client equivalence
 * checking and fallback wiring is CH-18b, task-1954).
 *
 * Only the 10 names IND-7g actually verified are ported (BBANDS is a
 * `VERIFIABLE_NAMES` candidate but fails 3-way verification at a param
 * boundary — see `verifiedIndicators.ts` module docstring — so it has no
 * kernel here at all, not merely an unpinned one).
 *
 * Decision (task-1953): no new OSS dependency (no `technicalindicators`,
 * no TA-Lib WASM) — every kernel here is written by hand from the Python
 * state machine so the numeric recipe (window re-sum every bar, SMA-seeded
 * EMA, Wilder smoothing, TA-Lib's exact 0-division rules) matches bit for
 * bit. klinecharts' bundled indicator math is never used as a substitute —
 * it isn't verified against the server (IND-7g) and would silently defeat
 * CH-18's purpose (server/client parity). `createClientIncrementalIndicator`
 * is the only entry point and it re-checks the whitelist itself — a caller
 * cannot bypass the gate by holding onto a kernel name.
 */

import type { IndicatorCatalogEntry } from "../plugins/indicatorPlugin";
import { resolveVerifiedIndicators } from "./verifiedIndicators";

export interface Bar {
  readonly open?: number;
  readonly high?: number;
  readonly low?: number;
  readonly close?: number;
  readonly volume?: number;
}

export type IndicatorParams = Readonly<Record<string, number>>;
export type IndicatorOutputs = Readonly<Record<string, number | null>>;

export interface IncrementalIndicator {
  readonly name: string;
  /** Feeds the next bar; returns one value per catalog output (null while lookback isn't met). */
  update(bar: Bar): IndicatorOutputs;
}

export type ClientEngineErrorCode =
  | "CLIENT_ENGINE_INDICATOR_NOT_VERIFIED"
  | "CLIENT_ENGINE_INDICATOR_UNKNOWN"
  | "CLIENT_ENGINE_PARAM_INVALID"
  | "CLIENT_ENGINE_INPUT_INVALID";

export class ClientEngineError extends Error {
  readonly code: ClientEngineErrorCode;
  readonly indicatorName: string;

  constructor(code: ClientEngineErrorCode, indicatorName: string, detail: string) {
    super(`${code}: ${detail} (indicator "${indicatorName}")`);
    this.name = "ClientEngineError";
    this.code = code;
    this.indicatorName = indicatorName;
  }
}

function requirePeriod(params: IndicatorParams, name: string, indicatorName: string): number {
  const value = params[name];
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    throw new ClientEngineError(
      "CLIENT_ENGINE_PARAM_INVALID",
      indicatorName,
      `param "${name}" must be a positive integer, got ${String(value)}`,
    );
  }
  return value;
}

function assertBarInputs(indicatorName: string, inputs: readonly string[], bar: Bar): void {
  const record = bar as Record<string, unknown>;
  for (const key of inputs) {
    const value = record[key];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new ClientEngineError(
        "CLIENT_ENGINE_INPUT_INVALID",
        indicatorName,
        `bar is missing finite input "${key}"`,
      );
    }
  }
}

/** Fixed-length window recomputed in full on every read (no running-sum drift, matches Python docstring). */
class Window {
  private readonly size: number;
  private readonly buf: number[] = [];

  constructor(size: number, indicatorName: string) {
    if (!Number.isInteger(size) || size <= 0) {
      throw new ClientEngineError("CLIENT_ENGINE_PARAM_INVALID", indicatorName, `window size must be a positive integer, got ${size}`);
    }
    this.size = size;
  }

  push(x: number): boolean {
    this.buf.push(x);
    if (this.buf.length > this.size) this.buf.shift();
    return this.buf.length === this.size;
  }

  total(): number {
    let sum = 0;
    for (const v of this.buf) sum += v;
    return sum;
  }

  mean(): number {
    return this.total() / this.size;
  }

  max(): number {
    let m = -Infinity;
    for (const v of this.buf) if (v > m) m = v;
    return m;
  }

  min(): number {
    let m = Infinity;
    for (const v of this.buf) if (v < m) m = v;
    return m;
  }

  meanAbsDev(center: number): number {
    let sum = 0;
    for (const v of this.buf) sum += Math.abs(v - center);
    return sum / this.size;
  }
}

/** SMA-seeded EMA; `skip` bars are discarded before the seed window starts filling (MACD fast line). */
class Ema {
  private readonly k: number;
  private readonly seed: Window;
  private skip: number;
  value: number | null = null;

  constructor(period: number, indicatorName: string, skip = 0) {
    this.k = 2.0 / (period + 1);
    this.seed = new Window(period, indicatorName);
    this.skip = skip;
  }

  push(x: number): number | null {
    if (this.skip > 0) {
      this.skip -= 1;
      return null;
    }
    if (this.value === null) {
      if (this.seed.push(x)) this.value = this.seed.mean();
      return this.value;
    }
    this.value = this.value + (x - this.value) * this.k;
    return this.value;
  }
}

/** Wilder smoothing: SMA seed over `period`, then `(prev * (period - 1) + x) / period`. */
class Wilder {
  private readonly period: number;
  private readonly seed: Window;
  value: number | null = null;

  constructor(period: number, indicatorName: string) {
    this.period = period;
    this.seed = new Window(period, indicatorName);
  }

  push(x: number): number | null {
    if (this.value === null) {
      if (this.seed.push(x)) this.value = this.seed.mean();
      return this.value;
    }
    this.value = (this.value * (this.period - 1) + x) / this.period;
    return this.value;
  }
}

interface KernelState {
  update(bar: Bar): readonly number[] | null;
}

type KernelFactory = (params: IndicatorParams, indicatorName: string) => KernelState;

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
      const { high, low, close, volume } = bar as Required<Pick<Bar, "high" | "low" | "close" | "volume">>;
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

/**
 * The only entry point into the client engine. Re-derives the whitelist from
 * `catalog` itself (never trusts a caller's say-so) and refuses to build a
 * kernel for anything outside it.
 */
export function createClientIncrementalIndicator(
  name: string,
  params: IndicatorParams,
  catalog: readonly IndicatorCatalogEntry[] | null | undefined,
): IncrementalIndicator {
  const verified = resolveVerifiedIndicators(catalog);
  const entry = verified.get(name);
  if (!entry) {
    throw new ClientEngineError(
      "CLIENT_ENGINE_INDICATOR_NOT_VERIFIED",
      name,
      "not in the server catalog's verified whitelist (missing, tier mismatch, or entry_hash drift) — client-side compute refused",
    );
  }
  const factory = KERNEL_FACTORIES[name];
  if (!factory) {
    throw new ClientEngineError("CLIENT_ENGINE_INDICATOR_UNKNOWN", name, "no ported client kernel for this indicator");
  }
  const state = factory(params, name);
  const outputs = entry.outputs;
  const inputs = entry.inputs;
  return {
    name,
    update(bar: Bar): IndicatorOutputs {
      assertBarInputs(name, inputs, bar);
      const values = state.update(bar);
      if (values === null) {
        return Object.fromEntries(outputs.map((output) => [output, null]));
      }
      return Object.fromEntries(outputs.map((output, index) => [output, values[index]!]));
    },
  };
}

export interface ComputeIndicatorSeriesArgs {
  readonly name: string;
  readonly params: IndicatorParams;
  readonly bars: readonly Bar[];
  readonly catalog: readonly IndicatorCatalogEntry[] | null | undefined;
}

export type IndicatorSeriesResult = Readonly<Record<string, ReadonlyArray<number | null>>>;

/** Batch helper over `createClientIncrementalIndicator` — the task body a `workerPool.ts` backend runs. */
export function computeIndicatorSeries(args: ComputeIndicatorSeriesArgs): IndicatorSeriesResult {
  const indicator = createClientIncrementalIndicator(args.name, args.params, args.catalog);
  const series: Record<string, (number | null)[]> = {};
  for (const bar of args.bars) {
    const values = indicator.update(bar);
    for (const [key, value] of Object.entries(values)) {
      (series[key] ??= []).push(value);
    }
  }
  return series;
}

/** Task name a `workerPool.ts` backend registers `computeIndicatorSeries` under. */
export const CLIENT_ENGINE_COMPUTE_TASK = "chart-engine/compute-indicator-series";
