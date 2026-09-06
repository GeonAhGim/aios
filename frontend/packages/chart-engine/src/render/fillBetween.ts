/**
 * CH-15 — fills the area between two value-space series (a `PlotSpec`'s
 * output and its `fill_between` partner). Pure value-space math: no pixel
 * projection, no scale/pane concept (that is `scaleBinding.ts` and
 * `plotRenderers.ts`'s job) — this module only decides *where* the two
 * series cross so each contiguous stretch can be filled and colored on its
 * own (bollinger band never crosses, so it always yields one segment;
 * ichimoku cloud crosses repeatedly, so bullish/bearish stretches split into
 * separate segments the caller can color differently).
 */

export interface FillSeriesPoint {
  readonly time: number;
  readonly value: number;
}

export interface FillSegmentPoint {
  readonly time: number;
  readonly base: number;
  readonly target: number;
}

export type FillDirection = "above" | "below" | "equal";

export interface FillSegment {
  readonly direction: FillDirection;
  readonly points: readonly FillSegmentPoint[];
}

export type FillBetweenErrorCode = "FILL_BETWEEN_LENGTH_MISMATCH" | "FILL_BETWEEN_TIME_MISMATCH";

export class FillBetweenError extends Error {
  readonly code: FillBetweenErrorCode;

  constructor(code: FillBetweenErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "FillBetweenError";
    this.code = code;
  }
}

function directionOf(base: number, target: number): FillDirection {
  if (base > target) return "above";
  if (base < target) return "below";
  return "equal";
}

/**
 * Interpolates the exact crossing point between `(t1, base1, target1)` and
 * `(t2, base2, target2)` so segment boundaries sit precisely at the crossing
 * instead of one bar early/late.
 */
function crossingPoint(t1: number, base1: number, target1: number, t2: number, base2: number, target2: number): FillSegmentPoint {
  const diff1 = base1 - target1;
  const diff2 = base2 - target2;
  const ratio = diff1 / (diff1 - diff2);
  const time = t1 + (t2 - t1) * ratio;
  const base = base1 + (base2 - base1) * ratio;
  const target = target1 + (target2 - target1) * ratio;
  return { time, base, target };
}

/**
 * Merges `base`/`target` into contiguous `FillSegment`s. Fail-closed: the two
 * series must be the same length and share the same timestamps pairwise —
 * silently zipping mismatched series would fill a polygon between unrelated
 * points.
 */
export function computeFillSegments(base: readonly FillSeriesPoint[], target: readonly FillSeriesPoint[]): FillSegment[] {
  if (base.length !== target.length) {
    throw new FillBetweenError(
      "FILL_BETWEEN_LENGTH_MISMATCH",
      `base has ${base.length} points, target has ${target.length}`,
    );
  }
  for (let i = 0; i < base.length; i++) {
    if (base[i]!.time !== target[i]!.time) {
      throw new FillBetweenError("FILL_BETWEEN_TIME_MISMATCH", `time mismatch at index ${i}: ${base[i]!.time} != ${target[i]!.time}`);
    }
  }
  if (base.length === 0) return [];

  const segments: FillSegment[] = [];
  let currentDirection: FillDirection = directionOf(base[0]!.value, target[0]!.value);
  let currentPoints: FillSegmentPoint[] = [{ time: base[0]!.time, base: base[0]!.value, target: target[0]!.value }];

  for (let i = 1; i < base.length; i++) {
    const prevBase = base[i - 1]!;
    const prevTarget = target[i - 1]!;
    const curBase = base[i]!;
    const curTarget = target[i]!;
    const nextDirection = directionOf(curBase.value, curTarget.value);

    const crossed =
      currentDirection !== "equal" &&
      nextDirection !== "equal" &&
      nextDirection !== currentDirection;

    if (crossed) {
      const crossing = crossingPoint(prevBase.time, prevBase.value, prevTarget.value, curBase.time, curBase.value, curTarget.value);
      currentPoints.push(crossing);
      segments.push({ direction: currentDirection, points: currentPoints });
      currentPoints = [crossing];
      currentDirection = nextDirection;
    } else if (nextDirection !== "equal" && currentDirection === "equal") {
      currentDirection = nextDirection;
    }
    currentPoints.push({ time: curBase.time, base: curBase.value, target: curTarget.value });
  }
  segments.push({ direction: currentDirection, points: currentPoints });
  return segments;
}
