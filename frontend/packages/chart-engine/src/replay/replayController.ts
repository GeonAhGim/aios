/**
 * CH-7 — replay controller: plays a CH-2 `CandleStream` bar by bar
 * (play/pause/step/seek/speed) over the same data path a backtest uses.
 *
 * Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2 (파일표
 * 66행), §9.6 CH-7. Data source is ONLY the injected `CandleStream` (LA-24
 * `candles` / `candles/replay` pages parsed by shared-types `parseCandleSeries`
 * and merged by `createCandleStream`) — no fetch, no parser, no re-sorting here.
 * Ordering, de-duplication and the as_of cut are decided upstream; this module
 * merely walks the resulting sequence.
 *
 * Look-ahead ban (BT-10 `BarWindow`: `columns[0:end)` only, later index →
 * `LookAheadError`): the cursor is the open_time of the last visible bar and a
 * frame's `visible` window never contains a bar after it. Only confirmed bars
 * (close_time <= as_of, CH-2 flag) take part — a forming bar is never replayed,
 * because a backtest never sees a partial candle.
 *
 * Determinism: no `Date`, no global timers — a `ReplayClock` is injected so the
 * tick cadence (`barIntervalMs / speed`) is reproducible in tests. Prices stay
 * contract Decimal strings (INVARIANTS.md I-05: same data, same rules as backtest).
 */

import type { CandleStream, CandleStreamSnapshot, StreamCandle } from "../data/candleStream";

/** Opaque timer handle returned by the injected clock. */
export type ReplayTimer = unknown;

/** Injected timer source (real `globalThis` clock in the app, a fake in tests). */
export interface ReplayClock {
  setTimeout(callback: () => void, delayMs: number): ReplayTimer;
  clearTimeout(timer: ReplayTimer): void;
}

export type ReplayStatus = "paused" | "playing";
export type ReplayCause = "play" | "tick" | "pause" | "step" | "seek" | "speed" | "stream" | "end";

export interface ReplayState {
  readonly status: ReplayStatus;
  /** Playback multiplier: one bar every `barIntervalMs / speed` clock ms. */
  readonly speed: number;
  /** open_time (epoch ms) of the last visible bar; null before the first bar. */
  readonly cursorTs: number | null;
  /** Number of visible bars == BT-10 `BarWindow.__len__`. */
  readonly visibleCount: number;
  /** Confirmed bars currently loaded in the stream. */
  readonly totalCount: number;
  readonly atEnd: boolean;
}

export interface ReplayFrame {
  readonly cause: ReplayCause;
  readonly state: ReplayState;
  /** Bars up to and including the cursor — the window a strategy would get. */
  readonly visible: readonly StreamCandle[];
  /** Bars newly revealed by this transition (forward moves only), open_time ascending. */
  readonly revealed: readonly StreamCandle[];
}

export interface ReplayController {
  play(): void;
  pause(): void;
  /** Moves the cursor one bar forward (+1) or back (-1); no-op at the edges. */
  step(delta: 1 | -1): void;
  /** Cursor becomes the last bar whose open_time <= `openTimeMs` (none → before first bar). */
  seek(openTimeMs: number): void;
  setSpeed(speed: number): void;
  state(): ReplayState;
  subscribe(listener: (frame: ReplayFrame) => void): () => void;
  dispose(): void;
}

export interface CreateReplayControllerOptions {
  readonly clock: ReplayClock;
  /** Initial multiplier (> 0, finite). Default 1. */
  readonly speed?: number;
  /** Clock ms per bar at speed 1 (> 0, finite). Default 1000. */
  readonly barIntervalMs?: number;
}

const DEFAULT_BAR_INTERVAL_MS = 1000;

function assertPositiveFinite(name: string, value: number): void {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new RangeError(`${name} must be a finite number > 0, got ${String(value)}`);
  }
}

/** Number of bars with openTimeMs <= t (binary search on the sorted universe). */
function countUpTo(bars: readonly StreamCandle[], t: number | null): number {
  if (t === null) return 0;
  let lo = 0;
  let hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (bars[mid]!.openTimeMs <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Replay universe: only bars the stream marks confirmed (closed as of the page's as_of). */
function confirmedBars(snapshot: CandleStreamSnapshot): StreamCandle[] {
  return snapshot.candles.filter((c) => c.confirmed);
}

export function createReplayController(
  stream: CandleStream,
  options: CreateReplayControllerOptions,
): ReplayController {
  const clock = options.clock;
  const barIntervalMs = options.barIntervalMs ?? DEFAULT_BAR_INTERVAL_MS;
  assertPositiveFinite("barIntervalMs", barIntervalMs);
  let speed = options.speed ?? 1;
  assertPositiveFinite("speed", speed);

  let bars: StreamCandle[] = confirmedBars(stream.snapshot());
  let cursorTs: number | null = null;
  let status: ReplayStatus = "paused";
  let timer: ReplayTimer | null = null;
  let disposed = false;
  const listeners = new Set<(frame: ReplayFrame) => void>();

  function visibleCount(): number {
    return countUpTo(bars, cursorTs);
  }

  function state(): ReplayState {
    const count = visibleCount();
    return { status, speed, cursorTs, visibleCount: count, totalCount: bars.length, atEnd: count >= bars.length };
  }

  function emit(cause: ReplayCause, revealed: readonly StreamCandle[]): void {
    const frame: ReplayFrame = { cause, state: state(), visible: bars.slice(0, visibleCount()), revealed };
    for (const listener of listeners) listener(frame);
  }

  function assertLive(): void {
    if (disposed) throw new Error("ReplayController is disposed");
  }

  function clearTimer(): void {
    if (timer !== null) {
      clock.clearTimeout(timer);
      timer = null;
    }
  }

  /** Moves the cursor to `bars[count - 1]`; returns the bars that became visible. */
  function moveTo(count: number): StreamCandle[] {
    const before = visibleCount();
    const clamped = Math.max(0, Math.min(count, bars.length));
    cursorTs = clamped === 0 ? null : bars[clamped - 1]!.openTimeMs;
    return clamped > before ? bars.slice(before, clamped) : [];
  }

  function schedule(): void {
    timer = clock.setTimeout(tick, barIntervalMs / speed);
  }

  function tick(): void {
    timer = null;
    if (disposed || status !== "playing") return;
    const count = visibleCount();
    if (count >= bars.length) {
      status = "paused";
      emit("end", []);
      return;
    }
    const revealed = moveTo(count + 1);
    if (visibleCount() >= bars.length) {
      status = "paused";
      emit("tick", revealed);
      emit("end", []);
      return;
    }
    emit("tick", revealed);
    if (status === "playing") schedule();
  }

  function play(): void {
    assertLive();
    if (status === "playing") return;
    if (visibleCount() >= bars.length) {
      emit("end", []);
      return;
    }
    status = "playing";
    emit("play", []);
    schedule();
  }

  function pause(): void {
    assertLive();
    clearTimer();
    if (status === "paused") return;
    status = "paused";
    emit("pause", []);
  }

  function step(delta: 1 | -1): void {
    assertLive();
    if (delta !== 1 && delta !== -1) throw new RangeError(`step delta must be 1 or -1, got ${String(delta)}`);
    const count = visibleCount();
    const target = count + delta;
    if (target < 0 || target > bars.length) return;
    emit("step", moveTo(target));
  }

  function seek(openTimeMs: number): void {
    assertLive();
    if (typeof openTimeMs !== "number" || !Number.isFinite(openTimeMs)) {
      throw new RangeError(`seek target must be a finite epoch-ms number, got ${String(openTimeMs)}`);
    }
    emit("seek", moveTo(countUpTo(bars, openTimeMs)));
  }

  function setSpeed(next: number): void {
    assertLive();
    assertPositiveFinite("speed", next);
    speed = next;
    if (status === "playing") {
      clearTimer();
      schedule();
    }
    emit("speed", []);
  }

  /** Stream changes (new page, realtime, reset) re-derive the universe; the cursor time is kept. */
  function onStream(snapshot: CandleStreamSnapshot): void {
    if (disposed) return;
    bars = confirmedBars(snapshot);
    if (bars.length === 0) cursorTs = null;
    emit("stream", []);
  }

  const unsubscribeStream = stream.subscribe(onStream);

  function subscribe(listener: (frame: ReplayFrame) => void): () => void {
    assertLive();
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    clearTimer();
    status = "paused";
    unsubscribeStream();
    listeners.clear();
  }

  return { play, pause, step, seek, setSpeed, state, subscribe, dispose };
}
