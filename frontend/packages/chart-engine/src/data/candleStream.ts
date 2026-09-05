/**
 * CH-2 — candle stream: merges LA-24 `GET /v1/foundation/market-data/candles`
 * pages (parsed upstream by shared-types `parseCandleSeries`, task-629) with
 * realtime updates into one open_time-monotone, de-duplicated series.
 *
 * Pure TS: no renderer, no fetch, no timers. The realtime feed is injected as
 * a `RealtimeCandleSource` (DC-18 unfinished — no transport/endpoint assumed).
 * Prices stay as contract Decimal strings; only timestamps are parsed to
 * epoch-ms for ordering. Numeric `CandlePoint` mapping belongs to CH-6.
 *
 * Fail-closed rules (INVARIANTS.md I-07/I-10 applied to data):
 *  - a page is validated whole and merged atomically (one bad candle rejects
 *    the page; nothing is half-applied);
 *  - same open_time → newest value replaces, EXCEPT a confirmed (closed)
 *    candle is never regressed by a forming or conflicting value;
 *  - gaps are never interpolated: server `gaps` and uncovered ranges between
 *    loaded pages/realtime are exposed as `GapMarker`s;
 *  - every rejection is returned to the caller and kept in the snapshot.
 */

import type { Adjustment, CandleGap, CandleRecord, ParsedCandleSeries, SeriesKey } from "@aios/shared-types";

export interface StreamCandle {
  readonly openTimeMs: number;
  readonly record: CandleRecord;
  /** true once the candle is closed (page: close_time <= as_of; realtime: source says so). */
  readonly confirmed: boolean;
}

/** [startMs, endMs) with no candles. `server` = CandleSeries.gaps; `uncovered` = never loaded. */
export interface GapMarker {
  readonly startMs: number;
  readonly endMs: number;
  readonly source: "server" | "uncovered";
}

export interface RealtimeCandleUpdate {
  readonly candle: CandleRecord;
  /** false while the candle is still forming; true on close. */
  readonly confirmed: boolean;
}

/** Injected realtime feed (WebSocket/polling implementation lives elsewhere, post DC-18). */
export interface RealtimeCandleSource {
  subscribe(key: SeriesKey, onUpdate: (update: RealtimeCandleUpdate) => void): () => void;
}

export type CandleStreamRejectionCode =
  | "parse_invalid" | "unsupported_schema_version" | "key_mismatch" | "invalid_time"
  | "duplicate_conflict" | "confirmed_regression" | "disposed";

export interface CandleStreamRejection {
  readonly code: CandleStreamRejectionCode;
  readonly openTime: string | null;
  readonly detail: string;
}

export type ApplyResult =
  | { readonly ok: true; readonly inserted: number; readonly replaced: number }
  | { readonly ok: false; readonly rejection: CandleStreamRejection };
type MergeAction = "insert" | "replace";

export interface CandleStreamSnapshot {
  readonly key: SeriesKey;
  readonly adjustment: Adjustment;
  readonly candles: readonly StreamCandle[];
  readonly gaps: readonly GapMarker[];
  /** LA-24 `meta.page.next_cursor` of the most recent page, null when exhausted/unknown. */
  readonly nextCursor: string | null;
  readonly rejections: readonly CandleStreamRejection[];
}

export interface CandleStream {
  readonly key: SeriesKey;
  applyPage(parsed: ParsedCandleSeries, nextCursor?: string | null): ApplyResult;
  applyRealtime(update: RealtimeCandleUpdate): ApplyResult;
  connect(source: RealtimeCandleSource): void;
  subscribe(listener: (snapshot: CandleStreamSnapshot) => void): () => void;
  snapshot(): CandleStreamSnapshot;
  reset(): void;
  dispose(): void;
}

export interface CreateCandleStreamOptions {
  readonly key: SeriesKey;
  readonly adjustment?: Adjustment;
  /** How many rejections the snapshot retains (oldest dropped). */
  readonly maxRejections?: number;
}

type Interval = Pick<GapMarker, "startMs" | "endMs">;

function sameKey(a: SeriesKey, b: SeriesKey): boolean {
  return a.venue === b.venue && a.instrument_id === b.instrument_id && a.timeframe === b.timeframe;
}

const VALUE_FIELDS = ["close_time", "open", "high", "low", "close", "volume"] as const;
const BAD_TIME = "open_time/close_time unusable or not chronological";

function sameRecord(a: CandleRecord, b: CandleRecord): boolean {
  return VALUE_FIELDS.every((f) => a[f] === b[f]) && (a.quote_volume ?? null) === (b.quote_volume ?? null);
}

function reject(code: CandleStreamRejectionCode, openTime: string | null, detail: string): ApplyResult {
  return { ok: false, rejection: { code, openTime, detail } };
}

/** First index whose openTimeMs >= t (binary search on a sorted array). */
function lowerBound(sorted: readonly StreamCandle[], t: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sorted[mid]!.openTimeMs < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Parses one contract candle; null when the timestamps are unusable. */
function toStreamCandle(record: CandleRecord, confirmed: boolean): StreamCandle | null {
  const openTimeMs = Date.parse(record.open_time);
  const closeTimeMs = Date.parse(record.close_time);
  if (!Number.isFinite(openTimeMs) || !Number.isFinite(closeTimeMs) || closeTimeMs <= openTimeMs) return null;
  return { openTimeMs, record, confirmed };
}

/** Union of intervals; touching or overlapping ones collapse. Input need not be sorted. */
function mergeIntervals(intervals: readonly Interval[]): Interval[] {
  const sorted = [...intervals].sort((a, b) => a.startMs - b.startMs);
  const out: Interval[] = [];
  for (const iv of sorted) {
    const last = out[out.length - 1];
    if (last && iv.startMs <= last.endMs) {
      if (iv.endMs > last.endMs) out[out.length - 1] = { startMs: last.startMs, endMs: iv.endMs };
    } else {
      out.push(iv);
    }
  }
  return out;
}

export function createCandleStream(options: CreateCandleStreamOptions): CandleStream {
  const key = options.key;
  const adjustment: Adjustment = options.adjustment ?? "RAW";
  const maxRejections = options.maxRejections ?? 32;

  let candles: StreamCandle[] = [];
  let coverage: Interval[] = [];
  let serverGaps = new Map<string, GapMarker>();
  let nextCursor: string | null = null;
  let rejections: CandleStreamRejection[] = [];
  const listeners = new Set<(snapshot: CandleStreamSnapshot) => void>();
  let unsubscribeSource: (() => void) | null = null;
  let disposed = false;

  function notify(): void {
    const snap = snapshot();
    for (const listener of listeners) listener(snap);
  }

  /** Every apply result passes here: rejections are retained, listeners always see the outcome. */
  function record(result: ApplyResult): ApplyResult {
    if (!result.ok) rejections = [...rejections, result.rejection].slice(-maxRejections);
    notify();
    return result;
  }

  /** Decides what happens to `incoming` given the existing candle at the same open_time. */
  function classify(incoming: StreamCandle): MergeAction | "noop" | ApplyResult {
    const idx = lowerBound(candles, incoming.openTimeMs);
    const existing = candles[idx];
    if (!existing || existing.openTimeMs !== incoming.openTimeMs) return "insert";
    if (sameRecord(existing.record, incoming.record)) {
      return existing.confirmed || !incoming.confirmed ? "noop" : "replace";
    }
    if (existing.confirmed) {
      const why = incoming.confirmed ? "conflicts with confirmed value" : "forming update over confirmed candle";
      return reject("confirmed_regression", incoming.record.open_time, why);
    }
    return "replace";
  }

  function commit(incoming: StreamCandle, action: MergeAction): void {
    const idx = lowerBound(candles, incoming.openTimeMs);
    if (action === "insert") candles.splice(idx, 0, incoming);
    else candles[idx] = incoming;
  }

  function coverOf(c: StreamCandle): Interval {
    return { startMs: c.openTimeMs, endMs: Date.parse(c.record.close_time) };
  }

  function applyPage(parsed: ParsedCandleSeries, cursor: string | null = null): ApplyResult {
    if (disposed) return record(reject("disposed", null, "stream is disposed"));
    if (parsed.kind === "unsupported_schema_version") {
      return record(reject("unsupported_schema_version", null, `received ${String(parsed.received)}`));
    }
    if (parsed.kind !== "ok") return record(reject("parse_invalid", null, "parseCandleSeries returned invalid"));
    const series = parsed.value;
    if (!sameKey(series.key, key) || series.adjustment !== adjustment) {
      return record(reject("key_mismatch", null, "series key/adjustment differs from stream"));
    }
    const asOfMs = Date.parse(series.as_of);
    if (!Number.isFinite(asOfMs)) return record(reject("invalid_time", null, `as_of unparseable: ${series.as_of}`));

    // Phase 1: validate the whole page (and its internal duplicates) without touching state.
    const staged = new Map<number, StreamCandle>();
    for (const rec of series.candles) {
      if (!sameKey(rec.key, key)) return record(reject("key_mismatch", rec.open_time, "candle key differs"));
      const closeMs = Date.parse(rec.close_time);
      const candle = toStreamCandle(rec, Number.isFinite(closeMs) && closeMs <= asOfMs);
      if (!candle) return record(reject("invalid_time", rec.open_time, BAD_TIME));
      const dup = staged.get(candle.openTimeMs);
      if (dup && !sameRecord(dup.record, rec)) {
        return record(reject("duplicate_conflict", rec.open_time, "same open_time with different values in one page"));
      }
      staged.set(candle.openTimeMs, candle);
    }
    const plan: Array<[StreamCandle, MergeAction]> = [];
    for (const candle of staged.values()) {
      const action = classify(candle);
      if (typeof action !== "string") return record(action);
      if (action !== "noop") plan.push([candle, action]);
    }

    // Phase 2: commit atomically.
    let inserted = 0;
    for (const [candle, action] of plan) {
      commit(candle, action);
      if (action === "insert") inserted += 1;
    }
    const gaps = series.gaps.map(
      (g: CandleGap): GapMarker => ({ startMs: Date.parse(g[0]), endMs: Date.parse(g[1]), source: "server" }),
    );
    for (const g of gaps) serverGaps.set(`${g.startMs}:${g.endMs}`, g);
    const pageSpan = [...staged.values()].map(coverOf);
    coverage = mergeIntervals([...coverage, ...pageSpan, ...gaps]);
    nextCursor = cursor;
    return record({ ok: true, inserted, replaced: plan.length - inserted });
  }

  function applyRealtime(update: RealtimeCandleUpdate): ApplyResult {
    if (disposed) return record(reject("disposed", null, "stream is disposed"));
    const rec = update.candle;
    if (!sameKey(rec.key, key)) return record(reject("key_mismatch", rec.open_time, "realtime candle key differs"));
    const candle = toStreamCandle(rec, update.confirmed);
    if (!candle) return record(reject("invalid_time", rec.open_time, BAD_TIME));
    const action = classify(candle);
    if (typeof action !== "string") return record(action);
    if (action === "noop") return record({ ok: true, inserted: 0, replaced: 0 });
    commit(candle, action);
    coverage = mergeIntervals([...coverage, coverOf(candle)]);
    return record({ ok: true, inserted: action === "insert" ? 1 : 0, replaced: action === "replace" ? 1 : 0 });
  }

  function uncoveredGaps(): GapMarker[] {
    const out: GapMarker[] = [];
    for (let i = 1; i < coverage.length; i += 1) {
      out.push({ startMs: coverage[i - 1]!.endMs, endMs: coverage[i]!.startMs, source: "uncovered" });
    }
    return out;
  }

  function snapshot(): CandleStreamSnapshot {
    const gaps = [...serverGaps.values(), ...uncoveredGaps()].sort((a, b) => a.startMs - b.startMs);
    return { key, adjustment, candles: [...candles], gaps, nextCursor, rejections: [...rejections] };
  }

  function connect(source: RealtimeCandleSource): void {
    if (disposed) throw new Error("CandleStream is disposed");
    unsubscribeSource?.();
    unsubscribeSource = source.subscribe(key, (update) => void applyRealtime(update));
  }

  function subscribe(listener: (snapshot: CandleStreamSnapshot) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function reset(): void {
    candles = [];
    coverage = [];
    serverGaps = new Map();
    nextCursor = null;
    rejections = [];
    notify();
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    unsubscribeSource?.();
    unsubscribeSource = null;
    listeners.clear();
  }

  return { key, applyPage, applyRealtime, connect, subscribe, snapshot, reset, dispose };
}
