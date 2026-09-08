/**
 * CH-18a primitives — bar/param types, the `ClientEngineError` taxonomy, and
 * the three numeric building blocks (`Window`, `Ema`, `Wilder`) that every
 * kernel in `kernelFactories.ts` is built from. Split out of `clientEngine.ts`
 * (task-2098, P6 300-line cap) with no behavior change.
 *
 * See `clientEngine.ts` module docstring for the CH-18 spec pointer and the
 * "no OSS indicator dependency" decision (task-1953) that governs why these
 * primitives are hand-written from the Python state machine rather than
 * borrowed from klinecharts or a TA library.
 */

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

export function requirePeriod(params: IndicatorParams, name: string, indicatorName: string): number {
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

export function assertBarInputs(indicatorName: string, inputs: readonly string[], bar: Bar): void {
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
export class Window {
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
export class Ema {
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
export class Wilder {
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

export interface KernelState {
  update(bar: Bar): readonly number[] | null;
}

export type KernelFactory = (params: IndicatorParams, indicatorName: string) => KernelState;
