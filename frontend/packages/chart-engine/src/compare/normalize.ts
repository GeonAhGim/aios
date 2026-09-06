// CH-13a — 멀티 심볼 비교 코어 (2/3): 기준봉 대비 백분율(base-100) 정규화.
//
// 서로 다른 통화·스케일의 두 심볼을 겹쳐 그리려면 원가격이 아니라 "기준 시각
// 대비 몇 %인가"로 바꿔야 한다. anchorTimeMs 후보의 종가가 없거나(결측) 0이면
// 전체 정규화가 무의미해지므로 값 대신 오류로 fail-closed 한다(0이나 NaN을
// 100%처럼 그리면 REJECT). 개별 봉(비-anchor)의 종가가 파싱 불가면 그 점만
// null로 남긴다 — 시리즈 전체를 버리지 않는다.

import type { CandleRecord } from "@aios/shared-types";

export interface NormalizedPoint {
  readonly timeMs: number;
  readonly time: string;
  /** anchor 종가를 100으로 둔 백분율. 이 봉의 종가를 숫자로 읽지 못하면 null. */
  readonly value: number | null;
}

export type NormalizeErrorCode = "empty_series" | "anchor_not_found" | "anchor_close_invalid";

export class NormalizeError extends Error {
  readonly code: NormalizeErrorCode;

  constructor(code: NormalizeErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "NormalizeError";
    this.code = code;
  }
}

function openTimeMs(record: CandleRecord): number {
  return Date.parse(record.open_time);
}

function toFiniteNumber(value: string): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Rebases `series` so the candle at `anchorTimeMs` reads 100. Throws `NormalizeError`
 * when the series is empty, no candle sits at `anchorTimeMs`, or the anchor's close is
 * missing/zero/non-numeric — an anchor that cannot define "unchanged" must not silently
 * produce a chartable value. */
export function normalizeToBase100(
  series: readonly CandleRecord[],
  anchorTimeMs: number,
): readonly NormalizedPoint[] {
  if (series.length === 0) throw new NormalizeError("empty_series", "series has no candles");

  const sorted = [...series].sort((a, b) => openTimeMs(a) - openTimeMs(b));
  const anchor = sorted.find((r) => openTimeMs(r) === anchorTimeMs);
  if (!anchor) throw new NormalizeError("anchor_not_found", `no candle at open_time ${anchorTimeMs}`);

  const anchorClose = toFiniteNumber(anchor.close);
  if (anchorClose === null || anchorClose === 0) {
    throw new NormalizeError("anchor_close_invalid", `anchor close is unusable: "${anchor.close}"`);
  }

  return sorted.map((record) => {
    const close = toFiniteNumber(record.close);
    return {
      timeMs: openTimeMs(record),
      time: record.open_time,
      value: close === null ? null : (close / anchorClose) * 100,
    };
  });
}
