/**
 * Maps a visible price range onto pixel-y for a viewport of `height`. Inverted
 * versus timeScale: higher price -> smaller y (screen coordinates grow down).
 * Same delegation seam as timeScale: a `PriceScaleBackend` (CH-1b: the vendored
 * klinecharts chart) answers first and the linear mapping is the fallback for
 * whatever it cannot resolve.
 */

export interface PriceRange {
  readonly min: number;
  readonly max: number;
}

/** Delegation seam. Return `null` when the backend cannot answer. */
export interface PriceScaleBackend {
  priceToY(price: number): number | null;
  yToPrice(y: number): number | null;
}

export interface PriceScale {
  readonly range: PriceRange;
  readonly height: number;
  setRange(range: PriceRange): void;
  setHeight(heightPx: number): void;
  priceToY(price: number): number;
  yToPrice(y: number): number;
}

const DEFAULT_RANGE: PriceRange = { min: 0, max: 1 };

function assertValidRange(range: PriceRange): void {
  if (!(range.max > range.min)) {
    throw new RangeError(`PriceRange.max (${range.max}) must be greater than min (${range.min})`);
  }
}

function assertValidHeight(height: number): void {
  if (height < 0) throw new RangeError(`PriceScale height must be non-negative, got ${height}`);
}

function isResolved(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export interface CreatePriceScaleOptions {
  range?: PriceRange;
  height?: number;
  backend?: PriceScaleBackend;
}

export function createPriceScale(options: CreatePriceScaleOptions = {}): PriceScale {
  let range = options.range ?? DEFAULT_RANGE;
  let height = options.height ?? 0;
  const backend = options.backend;
  assertValidRange(range);
  assertValidHeight(height);

  return {
    get range() {
      return range;
    },
    get height() {
      return height;
    },
    setRange(next) {
      assertValidRange(next);
      range = next;
    },
    setHeight(next) {
      assertValidHeight(next);
      height = next;
    },
    priceToY(price) {
      const delegated = backend?.priceToY(price);
      if (isResolved(delegated)) return delegated;
      if (height <= 0) return 0;
      const span = range.max - range.min;
      return (1 - (price - range.min) / span) * height;
    },
    yToPrice(y) {
      const delegated = backend?.yToPrice(y);
      if (isResolved(delegated)) return delegated;
      const span = range.max - range.min;
      if (height <= 0) return range.max;
      return range.max - (y / height) * span;
    },
  };
}
