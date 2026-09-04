/**
 * Maps a visible epoch-ms time range onto pixel-x for a viewport of `width`.
 * Pure linear mapping — CH-1b may delegate to vendor's own time-scale math
 * instead, but the contract here (range/width in, x/time out) must hold
 * regardless of backend.
 */

export interface TimeRange {
  readonly from: number;
  readonly to: number;
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

export interface CreateTimeScaleOptions {
  range?: TimeRange;
  width?: number;
}

export function createTimeScale(options: CreateTimeScaleOptions = {}): TimeScale {
  let range = options.range ?? DEFAULT_RANGE;
  let width = options.width ?? 0;
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
      if (width <= 0) return 0;
      const span = range.to - range.from;
      return ((time - range.from) / span) * width;
    },
    xToTime(x) {
      const span = range.to - range.from;
      if (width <= 0) return range.from;
      return range.from + (x / width) * span;
    },
  };
}
