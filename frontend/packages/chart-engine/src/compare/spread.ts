// CH-13a — 멀티 심볼 비교 코어 (3/3): 정렬 결과 위에서만 계산하는 스프레드.
//
// align.ts의 출력(`AlignedSeries`) 위에서만 동작한다 — 원시 배열을 다시 정렬하지
// 않는다. `points`가 비어있거나 timeMs가 엄격히 증가하지 않으면(정렬 결과가
// 아니라는 뜻) 예외를 던진다. 두 심볼의 견적 통화/스케일이 다르면(venue로 추정)
// 원가격 비교(ratio든 diff든) 자체가 무의미하므로 거부한다 — 먼저
// normalizeToBase100으로 정규화한 뒤 비교해야 한다.

import type { CandleRecord, SeriesKey, Venue } from "@aios/shared-types";
import type { AlignedSeries } from "./align";

export type SpreadMode = "ratio" | "diff";

export interface SpreadPoint {
  readonly timeMs: number;
  readonly time: string;
  /** ratio: other/base. diff: other - base. 둘 중 하나라도 없거나 숫자가 아니면 null. */
  readonly value: number | null;
}

export type SpreadErrorCode = "empty_aligned" | "misaligned" | "quote_scale_mismatch";

export class SpreadError extends Error {
  readonly code: SpreadErrorCode;

  constructor(code: SpreadErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "SpreadError";
    this.code = code;
  }
}

/** venue로부터 견적 통화/스케일을 추정한다 — CandleRecord에 통화 필드가 없으므로
 * SeriesKey.venue를 근사치로 쓴다(BITGET=USDT, KIS_KRX=KRW, KIS_US=USD). */
function quoteScaleOf(key: SeriesKey): string {
  const table: Record<Venue, string> = { BITGET: "USDT", KIS_KRX: "KRW", KIS_US: "USD" };
  return table[key.venue];
}

function toFiniteNumber(value: string): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function firstNonNull(records: readonly (CandleRecord | null)[]): CandleRecord | null {
  for (const r of records) if (r) return r;
  return null;
}

/** Computes a per-point spread over an `alignSeries` result. Throws `SpreadError` when
 * `aligned` is empty, its points are not strictly time-ordered (not a real alignment
 * result), or the two symbols' quote scales differ (raw-price comparison across
 * currencies/scales is rejected — normalize first). */
export function spread(aligned: AlignedSeries, mode: SpreadMode): readonly SpreadPoint[] {
  const { points } = aligned;
  if (points.length === 0) throw new SpreadError("empty_aligned", "aligned series has no points");

  for (let i = 1; i < points.length; i += 1) {
    if (points[i]!.timeMs <= points[i - 1]!.timeMs) {
      throw new SpreadError("misaligned", `points not strictly increasing at index ${i}`);
    }
  }

  const baseSample = firstNonNull(points.map((p) => p.base));
  const otherSample = firstNonNull(points.map((p) => p.other));
  if (baseSample && otherSample) {
    const baseScale = quoteScaleOf(baseSample.key);
    const otherScale = quoteScaleOf(otherSample.key);
    if (baseScale !== otherScale) {
      throw new SpreadError(
        "quote_scale_mismatch",
        `base=${baseScale} other=${otherScale}; normalize with normalizeToBase100 before comparing`,
      );
    }
  }

  return points.map((p) => {
    if (!p.base || !p.other) return { timeMs: p.timeMs, time: p.time, value: null };
    const base = toFiniteNumber(p.base.close);
    const other = toFiniteNumber(p.other.close);
    if (base === null || other === null) return { timeMs: p.timeMs, time: p.time, value: null };
    if (mode === "diff") return { timeMs: p.timeMs, time: p.time, value: other - base };
    if (base === 0) return { timeMs: p.timeMs, time: p.time, value: null };
    return { timeMs: p.timeMs, time: p.time, value: other / base };
  });
}
