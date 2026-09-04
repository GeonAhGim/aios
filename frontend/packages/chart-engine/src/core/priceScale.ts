/**
 * Maps a visible price range onto pixel-y for a viewport of `height`. Inverted
 * versus timeScale: higher price -> smaller y (screen coordinates grow down).
 * Same delegation seam as timeScale — pure math now, vendor-backed in CH-1b
 * if that turns out cheaper than re-deriving it.
 */

export interface PriceRange {
  readonly min: number;
  readonly max: number;
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

export interface CreatePriceScaleOptions {
  range?: PriceRange;
  height?: number;
}

export function createPriceScale(options: CreatePriceScaleOptions = {}): PriceScale {
  let range = options.range ?? DEFAULT_RANGE;
  let height = options.height ?? 0;
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
      if (height <= 0) return 0;
      const span = range.max - range.min;
      return (1 - (price - range.min) / span) * height;
    },
    yToPrice(y) {
      const span = range.max - range.min;
      if (height <= 0) return range.max;
      return range.max - (y / height) * span;
    },
  };
}
