import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseCandleSeries, type CandleRecord, type SeriesKey } from "@aios/shared-types";
import { describe, expect, it } from "vitest";
import { createCandleStream, type CandleStream, type StreamCandle } from "../../data/candleStream";
import { createReplayController, type ReplayClock, type ReplayFrame, type ReplayTimer } from "../replayController";

const KEY: SeriesKey = { venue: "BITGET", instrument_id: "0f3f1c2e-6b1a-4d3e-9a7b-2c8d5e4f1a90", timeframe: "1h" };
const H = 3_600_000;
const T0 = Date.parse("2026-09-01T00:00:00Z");
const AS_OF = "2026-09-01T06:00:00Z"; // bars 0..5 closed; bar 6 (close 07:00) is still forming

const iso = (ms: number): string => new Date(ms).toISOString().replace(".000Z", "Z");

function candle(i: number, overrides: Partial<CandleRecord> = {}): CandleRecord {
  return {
    key: KEY,
    open_time: iso(T0 + i * H),
    close_time: iso(T0 + (i + 1) * H),
    open: `${100 + i}.10`,
    high: `${110 + i}.25`,
    low: `${90 + i}.05`,
    close: `${105 + i}.5`,
    volume: `${i + 1}.000`,
    quote_volume: null,
    ...overrides,
  };
}

/** LA-24 `GET /candles/replay` ApiResponse envelope (ReplaySeriesView + meta), as the server sends it.
 * Order is scrambled, bar 2 appears twice (identical), bar 6 closes after as_of. */
const REPLAY_ENVELOPE = {
  data: {
    key: KEY,
    candles: [candle(3), candle(0), candle(6), candle(2), candle(5), candle(2), candle(1), candle(4)],
    gaps: [] as Array<[string, string]>,
    adjustment: "RAW",
    as_of: AS_OF,
    series_hash: "b1946ac92492d2347c6235b4d2611184",
    expected_count: 7,
    missing_count: 0,
    instrument_id: KEY.instrument_id,
    symbol: "BTCUSDT",
    canonical_symbol: "BTC/USDT",
    entitlement: { mode: "delayed", delayed_seconds: 900 },
    schema_version: "v1",
  },
  meta: { trace_id: "6d2f7c1a-0b3e-4f5a-8c9d-1e2f3a4b5c6d", as_of: AS_OF, page: null },
};

type Row = readonly [string, string, string, string, string, string, string];

/** What `read_candles_columnar` (open_time ASC, as_of cut) hands BT-10 for the same request:
 * ascending, de-duplicated, forming bar 6 excluded. Hand-written, not derived. */
const GOLDEN_BARS: readonly Row[] = [
  ["2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z", "100.10", "110.25", "90.05", "105.5", "1.000"],
  ["2026-09-01T01:00:00Z", "2026-09-01T02:00:00Z", "101.10", "111.25", "91.05", "106.5", "2.000"],
  ["2026-09-01T02:00:00Z", "2026-09-01T03:00:00Z", "102.10", "112.25", "92.05", "107.5", "3.000"],
  ["2026-09-01T03:00:00Z", "2026-09-01T04:00:00Z", "103.10", "113.25", "93.05", "108.5", "4.000"],
  ["2026-09-01T04:00:00Z", "2026-09-01T05:00:00Z", "104.10", "114.25", "94.05", "109.5", "5.000"],
  ["2026-09-01T05:00:00Z", "2026-09-01T06:00:00Z", "105.10", "115.25", "95.05", "110.5", "6.000"],
];

const row = (c: StreamCandle): Row => {
  const r = c.record;
  return [r.open_time, r.close_time, r.open, r.high, r.low, r.close, r.volume];
};

function createFakeClock() {
  let now = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; cb: () => void }>();
  const clock: ReplayClock = {
    setTimeout(cb, ms) {
      seq += 1;
      timers.set(seq, { at: now + ms, cb });
      return seq;
    },
    clearTimeout(t: ReplayTimer) {
      timers.delete(t as number);
    },
  };
  function advance(ms: number): void {
    const target = now + ms;
    for (;;) {
      let next: [number, { at: number; cb: () => void }] | null = null;
      for (const entry of timers) if (entry[1].at <= target && (!next || entry[1].at < next[1].at)) next = entry;
      if (!next) break;
      timers.delete(next[0]);
      now = next[1].at;
      next[1].cb();
    }
    now = target;
  }
  return { clock, advance, pending: () => timers.size };
}

function loadedStream(): CandleStream {
  const stream = createCandleStream({ key: KEY });
  const applied = stream.applyPage(parseCandleSeries(REPLAY_ENVELOPE));
  expect(applied).toEqual({ ok: true, inserted: 7, replaced: 0 });
  return stream;
}

function setup(options: { speed?: number; barIntervalMs?: number } = {}) {
  const fake = createFakeClock();
  const stream = loadedStream();
  const frames: ReplayFrame[] = [];
  const controller = createReplayController(stream, { clock: fake.clock, ...options });
  controller.subscribe((f) => frames.push(f));
  return { ...fake, stream, controller, frames };
}

const revealedRows = (frames: readonly ReplayFrame[]): Row[] => frames.flatMap((f) => f.revealed.map(row));

/** BT-10 BarWindow rule: nothing after the cursor is ever visible, and visible == bars[0, cursor]. */
function assertNoLookAhead(frames: readonly ReplayFrame[]): void {
  for (const f of frames) {
    const { cursorTs, visibleCount } = f.state;
    expect(f.visible).toHaveLength(visibleCount);
    expect(f.visible.every((c) => cursorTs !== null && c.openTimeMs <= cursorTs)).toBe(true);
    expect(f.revealed.every((c) => cursorTs !== null && c.openTimeMs <= cursorTs)).toBe(true);
    expect(f.visible.map(row)).toEqual(GOLDEN_BARS.slice(0, visibleCount));
  }
}

describe("replay = backtest bar sequence identity", () => {
  it("playing to the end reveals exactly the backtest columns (ascending, deduped, as_of cut)", () => {
    const { controller, frames, advance } = setup();
    controller.play();
    advance(1000 * 10);
    expect(revealedRows(frames)).toEqual(GOLDEN_BARS);
    expect(frames.at(-1)!.cause).toBe("end");
    expect(controller.state()).toMatchObject({ status: "paused", atEnd: true, visibleCount: 6, totalCount: 6 });
    assertNoLookAhead(frames);
  });

  it("each tick frame is the BarWindow(columns, i + 1) a strategy would receive", () => {
    const { controller, frames, advance } = setup();
    controller.play();
    for (let i = 0; i < GOLDEN_BARS.length; i += 1) {
      advance(1000);
      const f = frames.filter((x) => x.cause === "tick").at(-1)!;
      expect(f.visible.map(row)).toEqual(GOLDEN_BARS.slice(0, i + 1));
      expect(f.revealed.map(row)).toEqual([GOLDEN_BARS[i]]);
    }
  });

  it("step(+1) and seek(end) walk the same sequence as play", () => {
    const stepped = setup();
    for (let i = 0; i < 20; i += 1) stepped.controller.step(1);
    expect(revealedRows(stepped.frames)).toEqual(GOLDEN_BARS);
    const sought = setup();
    sought.controller.seek(Date.parse("2030-01-01T00:00:00Z"));
    expect(revealedRows(sought.frames)).toEqual(GOLDEN_BARS);
    assertNoLookAhead([...stepped.frames, ...sought.frames]);
  });

  it("the forming bar (close_time > as_of) is never replayed, even when seeking past it", () => {
    const { controller, frames } = setup();
    controller.seek(T0 + 6 * H + 1);
    expect(frames.at(-1)!.visible.map((c) => c.record.open_time)).not.toContain(iso(T0 + 6 * H));
    expect(controller.state().cursorTs).toBe(T0 + 5 * H);
  });
});

describe("look-ahead negative tests", () => {
  it("never emits a bar beyond the cursor across play/step/seek/stream mutations", () => {
    const { controller, frames, stream, advance } = setup();
    controller.seek(T0 + 2 * H + 1); // lands on bar 2 (last open_time <= target)
    expect(controller.state()).toMatchObject({ cursorTs: T0 + 2 * H, visibleCount: 3 });
    stream.applyRealtime({ candle: candle(7), confirmed: false }); // forming realtime → excluded
    stream.applyRealtime({ candle: candle(6, { close: "999" }), confirmed: true }); // closed later → after cursor
    expect(frames.at(-1)!.visible).toHaveLength(3);
    controller.play();
    advance(1000 * 2);
    expect(controller.state().visibleCount).toBe(5);
    controller.pause();
    advance(1000 * 50);
    expect(controller.state().visibleCount).toBe(5);
    assertNoLookAhead(frames);
    expect(frames.every((f) => f.visible.every((c) => c.record.open_time !== iso(T0 + 7 * H)))).toBe(true);
  });

  it("step(+1) at the end and step(-1) at the start are no-ops", () => {
    const { controller, frames } = setup();
    controller.step(-1);
    expect(frames).toHaveLength(0);
    controller.seek(T0 + 99 * H);
    const n = frames.length;
    controller.step(1);
    expect(frames).toHaveLength(n);
    expect(controller.state().atEnd).toBe(true);
  });

  it("an older page prepended later becomes visible without moving the cursor", () => {
    const { controller, stream, frames } = setup();
    controller.seek(T0 + 1 * H);
    const older = parseCandleSeries({
      ...REPLAY_ENVELOPE,
      data: { ...REPLAY_ENVELOPE.data, candles: [candle(-2), candle(-1)] },
    });
    expect(stream.applyPage(older)).toMatchObject({ ok: true, inserted: 2 });
    const f = frames.at(-1)!;
    expect(f.cause).toBe("stream");
    expect(f.revealed).toEqual([]);
    expect(f.state.cursorTs).toBe(T0 + 1 * H);
    expect(f.visible.map((c) => (c.openTimeMs - T0) / H)).toEqual([-2, -1, 0, 1]);
  });
});

describe("clock-driven playback", () => {
  it("advances one bar per barIntervalMs / speed and honours pause, setSpeed and resume", () => {
    const { controller, advance, pending } = setup({ barIntervalMs: 1000, speed: 1 });
    controller.play();
    advance(999);
    expect(controller.state().visibleCount).toBe(0);
    advance(1);
    expect(controller.state().visibleCount).toBe(1);
    controller.setSpeed(4);
    advance(250);
    expect(controller.state().visibleCount).toBe(2);
    controller.pause();
    expect(pending()).toBe(0);
    advance(10_000);
    expect(controller.state().visibleCount).toBe(2);
    controller.play();
    advance(250 * 4);
    expect(controller.state()).toMatchObject({ visibleCount: 6, status: "paused", atEnd: true });
    expect(pending()).toBe(0);
  });

  it("play at the end emits 'end' without scheduling; step(-1) then play resumes", () => {
    const { controller, frames, advance, pending } = setup();
    controller.seek(T0 + 5 * H);
    controller.play();
    expect(frames.at(-1)!.cause).toBe("end");
    expect(pending()).toBe(0);
    controller.step(-1);
    controller.play();
    advance(1000);
    expect(revealedRows(frames.filter((f) => f.cause === "tick").slice(-1))).toEqual([GOLDEN_BARS[5]]);
    expect(frames.at(-1)!.cause).toBe("end");
  });

  it("dispose clears the timer, detaches from the stream and rejects further commands", () => {
    const { controller, frames, stream, advance, pending } = setup();
    controller.play();
    controller.dispose();
    expect(pending()).toBe(0);
    const n = frames.length;
    advance(10_000);
    stream.applyRealtime({ candle: candle(6), confirmed: true });
    expect(frames).toHaveLength(n);
    expect(() => controller.play()).toThrow(/disposed/);
    expect(() => controller.seek(T0)).toThrow(/disposed/);
  });

  it("rejects non-positive or non-finite speed, interval and seek targets (fail-closed)", () => {
    const { controller, clock, stream } = setup();
    expect(() => controller.setSpeed(0)).toThrow(RangeError);
    expect(() => controller.setSpeed(Number.NaN)).toThrow(RangeError);
    expect(() => controller.seek(Number.POSITIVE_INFINITY)).toThrow(RangeError);
    expect(() => createReplayController(stream, { clock, speed: -1 })).toThrow(RangeError);
    expect(() => createReplayController(stream, { clock, barIntervalMs: 0 })).toThrow(RangeError);
    expect(controller.state().speed).toBe(1);
  });
});

describe("data-path wiring (I-10)", () => {
  it("the controller has no fetch and no parser of its own — CandleStream is the only source", () => {
    const source = readFileSync(fileURLToPath(new URL("../replayController.ts", import.meta.url)), "utf8");
    expect(source).not.toMatch(/\bfetch\s*\(/);
    expect(source).not.toMatch(/from "@aios\/shared-types"|\bparseCandleSeries\s*\(|JSON\.parse/);
    expect(source).toMatch(/from "\.\.\/data\/candleStream"/);
    expect(source).toMatch(/clock\.setTimeout\(/);
    expect(source).not.toMatch(/\bDate\.now\b|\bglobalThis\.|\bwindow\.|\bperformance\./);
  });
});

// DEEPEN(task-3075) of task-1539 (CH-7, commit 527e719), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original 12-test leaf had
// negative>=7 and backtest golden-sequence identity, but zero failure
// injection, no numeric performance assertion (the I-10 wiring check above is
// a static source grep, not a runtime measurement), no gate-red reproduction,
// and no D3 (adversarial/multi-instance) proof. This block fills those gaps.
describe("DEEPEN(task-3075): failure injection, numeric perf, gate-red, D3", () => {
  it("실패 주입: a clock.setTimeout failure propagates out of play() instead of being swallowed", () => {
    const stream = loadedStream();
    const throwingClock: ReplayClock = {
      setTimeout(): ReplayTimer {
        throw new Error("clock: timer subsystem exhausted");
      },
      clearTimeout(): void {},
    };
    const controller = createReplayController(stream, { clock: throwingClock });

    expect(() => controller.play()).toThrow(/timer subsystem exhausted/);
    // documents the current fail-open ordering: status flips to "playing" and the
    // "play" frame is emitted *before* schedule() calls the (throwing) clock.
    expect(controller.state().status).toBe("playing");
  });

  it("실패 주입: a throwing subscriber propagates out of seek() and stops later listeners, but the cursor it already moved is not rolled back", () => {
    const fake = createFakeClock();
    const stream = loadedStream();
    const controller = createReplayController(stream, { clock: fake.clock });
    const goodCalls: ReplayFrame[] = [];
    controller.subscribe(() => {
      throw new Error("listener: render failed");
    });
    controller.subscribe((f) => goodCalls.push(f));

    expect(() => controller.seek(T0 + 2 * H)).toThrow(/render failed/);
    expect(controller.state().cursorTs).toBe(T0 + 2 * H);
    expect(goodCalls).toHaveLength(0);
  });

  it("수치 성능: stepping through 5,000 confirmed bars one at a time stays under a 500ms budget", () => {
    const N = 5000;
    const asOfMs = T0 + (N + 1) * H;
    const records: CandleRecord[] = [];
    for (let i = 0; i < N; i += 1) records.push(candle(i));
    const parsed = parseCandleSeries({
      data: {
        key: KEY,
        candles: records,
        gaps: [] as Array<[string, string]>,
        adjustment: "RAW",
        as_of: iso(asOfMs),
        series_hash: "perf-bench",
        expected_count: N,
        missing_count: 0,
        instrument_id: KEY.instrument_id,
        symbol: "BTCUSDT",
        canonical_symbol: "BTC/USDT",
        entitlement: { mode: "delayed", delayed_seconds: 900 },
        schema_version: "v1",
      },
      meta: { trace_id: "6d2f7c1a-0b3e-4f5a-8c9d-1e2f3a4b5c6d", as_of: iso(asOfMs), page: null },
    });
    const stream = createCandleStream({ key: KEY });
    expect(stream.applyPage(parsed)).toMatchObject({ ok: true, inserted: N });
    const fake = createFakeClock();
    const controller = createReplayController(stream, { clock: fake.clock });

    const startedAt = performance.now();
    for (let i = 0; i < N; i += 1) controller.step(1);
    const elapsedMs = performance.now() - startedAt;

    expect(controller.state()).toMatchObject({ visibleCount: N, totalCount: N, atEnd: true });
    expect(elapsedMs).toBeLessThan(500);
  });

  it("게이트 적색 재현: the final tick frame already reports status paused and atEnd — flipping status after emit would go red", () => {
    const { controller, frames, advance } = setup();
    controller.play();
    advance(1000 * GOLDEN_BARS.length);
    const lastTick = frames.filter((f) => f.cause === "tick").at(-1)!;
    expect(lastTick.state.status).toBe("paused");
    expect(lastTick.state.atEnd).toBe(true);
  });

  it("게이트 적색 재현: two consecutive setSpeed calls while playing keep exactly one pending timer — a missing clearTimer would double-schedule and skip a bar", () => {
    const { controller, advance, pending } = setup({ barIntervalMs: 1000, speed: 1 });
    controller.play();
    controller.setSpeed(2);
    controller.setSpeed(4);
    expect(pending()).toBe(1);
    advance(250); // 1000 / 4
    expect(controller.state().visibleCount).toBe(1);
  });

  it("어드버서리얼(D3): two controllers over independent streams never share cursor or timer state", () => {
    const a = setup();
    const b = setup();
    a.controller.seek(T0 + 1 * H);
    expect(b.controller.state().cursorTs).toBeNull();
    a.controller.play();
    a.advance(1000);
    expect(b.pending()).toBe(0);
    a.controller.dispose();
    expect(() => b.controller.play()).not.toThrow();
  });

  it("어드버서리얼(D3): two controllers sharing one CandleStream keep independent cursors, and disposing one leaves the other's stream subscription intact", () => {
    const fake = createFakeClock();
    const stream = loadedStream();
    const frames2: ReplayFrame[] = [];
    const c1 = createReplayController(stream, { clock: fake.clock });
    const c2 = createReplayController(stream, { clock: fake.clock });
    c2.subscribe((f) => frames2.push(f));

    c1.seek(T0 + 3 * H);
    expect(c2.state().cursorTs).toBeNull();

    c1.dispose();
    stream.applyRealtime({ candle: candle(6, { close: "999" }), confirmed: true });
    expect(frames2.at(-1)!.cause).toBe("stream");
    expect(c2.state().totalCount).toBe(7);
  });
});
