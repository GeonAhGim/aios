/**
 * Maps a visible epoch-ms time range onto pixel-x for a viewport of `width`.
 * Pure linear mapping by default. When a `TimeScaleBackend` is supplied
 * (CH-1b: the vendored klinecharts chart) coordinate conversion is delegated
 * to it first; the linear mapping is the fallback for whatever the backend
 * cannot resolve (no chart mounted, no data yet). The contract here
 * (range/width in, x/time out) holds regardless of backend.
 */

export interface TimeRange {
  readonly from: number;
  readonly to: number;
}

/** Delegation seam. Return `null` when the backend cannot answer. */
export interface TimeScaleBackend {
  timeToX(time: number): number | null;
  xToTime(x: number): number | null;
}

export interface TimeScale {
  readonly range: TimeRange;
  readonly width: number;
  setRange(range: TimeRange): void;
  setWidth(widthPx: number): void;
  timeToX(time: number): number;
  xToTime(x: number): number;
}

const DEFAULT_RANGE: TimeRange = { from: 0, to: 1 };

function assertValidRange(range: TimeRange): void {
  if (!(range.to > range.from)) {
    throw new RangeError(`TimeRange.to (${range.to}) must be greater than from (${range.from})`);
  }
}

function assertValidWidth(width: number): void {
  if (width < 0) throw new RangeError(`TimeScale width must be non-negative, got ${width}`);
}

function isResolved(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export interface CreateTimeScaleOptions {
  range?: TimeRange;
  width?: number;
  backend?: TimeScaleBackend;
}

export function createTimeScale(options: CreateTimeScaleOptions = {}): TimeScale {
  let range = options.range ?? DEFAULT_RANGE;
  let width = options.width ?? 0;
  const backend = options.backend;
  assertValidRange(range);
  assertValidWidth(width);

  return {
    get range() {
      return range;
    },
    get width() {
      return width;
    },
    setRange(next) {
      assertValidRange(next);
      range = next;
    },
    setWidth(next) {
      assertValidWidth(next);
      width = next;
    },
    timeToX(time) {
      const delegated = backend?.timeToX(time);
      if (isResolved(delegated)) return delegated;
      if (width <= 0) return 0;
      const span = range.to - range.from;
      return ((time - range.from) / span) * width;
    },
    xToTime(x) {
      const delegated = backend?.xToTime(x);
      if (isResolved(delegated)) return delegated;
      const span = range.to - range.from;
      if (width <= 0) return range.from;
      return range.from + (x / width) * span;
    },
  };
}
