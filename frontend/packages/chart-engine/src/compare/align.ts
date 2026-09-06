// CH-13a — 멀티 심볼 비교 코어 (1/3): 타임스탬프 기준 정렬.
//
// 순수 계산만 한다 — fetch·React·전역시각 금지(시각은 인자로만 받는다). 입력은
// candleSeries 파서(task-629, `@aios/shared-types`)가 만든 `CandleRecord[]`를
// 그대로 소비하고, 가격 문자열은 여기서 손대지 않는다(Decimal 계약 보존).
//
// "정렬"은 두 시리즈의 커버 구간 교집합(겹치는 시간대)을 구하고, 그 구간 안에서
// 어느 한쪽에만 존재하는 타임스탬프는 없는 쪽을 null로 남긴다. 직전값으로
// 전진충전(forward-fill)하지 않는다 — 없는 데이터를 있는 것처럼 그리면 안 된다.

import type { CandleRecord } from "@aios/shared-types";

export interface AlignedPoint {
  readonly timeMs: number;
  readonly time: string;
  readonly base: CandleRecord | null;
  readonly other: CandleRecord | null;
}

export interface AlignedSeries {
  readonly points: readonly AlignedPoint[];
}

export type AlignErrorCode = "empty_base" | "empty_other" | "no_overlap";

export class AlignError extends Error {
  readonly code: AlignErrorCode;

  constructor(code: AlignErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "AlignError";
    this.code = code;
  }
}

function openTimeMs(record: CandleRecord): number {
  return Date.parse(record.open_time);
}

function sortByOpenTime(records: readonly CandleRecord[]): CandleRecord[] {
  return [...records].sort((a, b) => openTimeMs(a) - openTimeMs(b));
}

/** Aligns `base`/`other` on open_time within their overlapping time range. Throws
 * `AlignError` when either series is empty or their ranges/candles never overlap —
 * a caller must not silently render an empty or one-sided comparison. */
export function alignSeries(base: readonly CandleRecord[], other: readonly CandleRecord[]): AlignedSeries {
  if (base.length === 0) throw new AlignError("empty_base", "base series has no candles");
  if (other.length === 0) throw new AlignError("empty_other", "other series has no candles");

  const baseSorted = sortByOpenTime(base);
  const otherSorted = sortByOpenTime(other);

  const overlapStart = Math.max(openTimeMs(baseSorted[0]!), openTimeMs(otherSorted[0]!));
  const overlapEnd = Math.min(
    openTimeMs(baseSorted[baseSorted.length - 1]!),
    openTimeMs(otherSorted[otherSorted.length - 1]!),
  );
  if (overlapStart > overlapEnd) throw new AlignError("no_overlap", "base/other time ranges do not overlap");

  const baseByTime = new Map<number, CandleRecord>();
  for (const record of baseSorted) {
    const t = openTimeMs(record);
    if (t >= overlapStart && t <= overlapEnd) baseByTime.set(t, record);
  }
  const otherByTime = new Map<number, CandleRecord>();
  for (const record of otherSorted) {
    const t = openTimeMs(record);
    if (t >= overlapStart && t <= overlapEnd) otherByTime.set(t, record);
  }

  const times = [...new Set([...baseByTime.keys(), ...otherByTime.keys()])].sort((a, b) => a - b);

  const points: AlignedPoint[] = times.map((t) => ({
    timeMs: t,
    time: new Date(t).toISOString(),
    base: baseByTime.get(t) ?? null,
    other: otherByTime.get(t) ?? null,
  }));

  return { points };
}
