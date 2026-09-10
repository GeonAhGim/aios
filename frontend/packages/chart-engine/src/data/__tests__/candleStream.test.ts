import { parseCandleSeries, type CandleRecord, type SeriesKey } from "@aios/shared-types";
import { describe, expect, it } from "vitest";
import {
  createCandleStream,
  type CandleStreamSnapshot,
  type RealtimeCandleSource,
  type RealtimeCandleUpdate,
} from "../candleStream";

const KEY: SeriesKey = { venue: "BITGET", instrument_id: "i-1", timeframe: "1h" };
const H = 3_600_000;
const T0 = Date.parse("2026-09-01T00:00:00Z");

function iso(ms: number): string {
  return new Date(ms).toISOString().replace(".000Z", "Z");
}

function candle(i: number, overrides: Partial<CandleRecord> = {}): CandleRecord {
  return {
    key: KEY,
    open_time: iso(T0 + i * H),
    close_time: iso(T0 + (i + 1) * H),
    open: "100",
    high: "110",
    low: "90",
    close: "105",
    volume: "1",
    quote_volume: null,
    ...overrides,
  };
}

interface PageOptions {
  asOfIndex?: number;
  gaps?: Array<[number, number]>;
  key?: SeriesKey;
  adjustment?: string;
}

/** Builds a raw LA-24 body and runs it through the real task-629 parser (no parser re-implementation). */
function page(candles: CandleRecord[], options: PageOptions = {}) {
  const lastIdx = candles.reduce((max, c) => Math.max(max, (Date.parse(c.open_time) - T0) / H), 0);
  const asOfMs = T0 + (options.asOfIndex ?? lastIdx + 1) * H;
  return parseCandleSeries({
    key: options.key ?? KEY,
    candles,
    gaps: (options.gaps ?? []).map(([a, b]) => [iso(T0 + a * H), iso(T0 + b * H)]),
    adjustment: options.adjustment ?? "RAW",
    as_of: iso(asOfMs),
    series_hash: "h",
    schema_version: "v1",
  });
}

function range(from: number, to: number): CandleRecord[] {
  return Array.from({ length: to - from }, (_, i) => candle(from + i));
}

function indices(snap: CandleStreamSnapshot): number[] {
  return snap.candles.map((c) => (c.openTimeMs - T0) / H);
}

function realtime(i: number, confirmed: boolean, overrides: Partial<CandleRecord> = {}): RealtimeCandleUpdate {
  return { candle: candle(i, overrides), confirmed };
}

/** mulberry32 — deterministic shuffle so a failing seed is reproducible. */
function shuffled<T>(items: readonly T[], seed: number): T[] {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}

describe("candleStream pagination merge", () => {
  it("prepends an older page before a newer one in open_time order", () => {
    const stream = createCandleStream({ key: KEY });
    expect(stream.applyPage(page(range(10, 20)))).toEqual({ ok: true, inserted: 10, replaced: 0 });
    expect(stream.applyPage(page(range(0, 10)))).toEqual({ ok: true, inserted: 10, replaced: 0 });
    expect(indices(stream.snapshot())).toEqual(Array.from({ length: 20 }, (_, i) => i));
  });

  it("sorts a page delivered in reverse order and keeps Decimal strings untouched", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 8).reverse()));
    const snap = stream.snapshot();
    expect(indices(snap)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(snap.candles[0]!.record.open).toBe("100");
    expect(snap.candles.every((c) => c.confirmed)).toBe(true);
  });

  it("is deterministic under seeded shuffles of pages and candles within pages", () => {
    const canonical = createCandleStream({ key: KEY });
    const pages = [range(0, 5), range(5, 10), range(10, 15), range(15, 20)];
    for (const p of pages) canonical.applyPage(page(p));
    const expected = canonical.snapshot();
    for (const seed of [1, 7, 42, 1234, 99999]) {
      const stream = createCandleStream({ key: KEY });
      for (const p of shuffled(pages, seed)) stream.applyPage(page(shuffled(p, seed + 1)));
      const snap = stream.snapshot();
      expect(indices(snap), `seed ${seed}`).toEqual(indices(expected));
      expect(snap.gaps, `seed ${seed}`).toEqual(expected.gaps);
    }
  });

  it("dedupes identical duplicates and rejects a conflicting duplicate page atomically", () => {
    const stream = createCandleStream({ key: KEY });
    expect(stream.applyPage(page([candle(3), candle(3), candle(4)]))).toEqual({ ok: true, inserted: 2, replaced: 0 });
    const result = stream.applyPage(page([candle(7), candle(8), candle(8, { close: "999" })]));
    expect(result).toMatchObject({ ok: false, rejection: { code: "duplicate_conflict", openTime: iso(T0 + 8 * H) } });
    expect(indices(stream.snapshot())).toEqual([3, 4]);
  });

  it("keeps the page candle closing after as_of unconfirmed so a later page may replace it", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 3), { asOfIndex: 2.5 }));
    expect(stream.snapshot().candles.map((c) => c.confirmed)).toEqual([true, true, false]);
    expect(stream.applyPage(page([candle(2, { close: "200" })]))).toEqual({ ok: true, inserted: 0, replaced: 1 });
    const last = stream.snapshot().candles[2]!;
    expect(last.record.close).toBe("200");
    expect(last.confirmed).toBe(true);
  });

  it("tracks nextCursor of the latest page", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 2)), "2026-09-01T02:00:00Z");
    expect(stream.snapshot().nextCursor).toBe("2026-09-01T02:00:00Z");
    stream.applyPage(page(range(2, 4)), null);
    expect(stream.snapshot().nextCursor).toBeNull();
    stream.applyPage(page(range(4, 5)));
    expect(stream.snapshot().nextCursor).toBeNull();
  });
});

describe("candleStream realtime merge", () => {
  it("upserts a forming candle with the newest value and promotes it on confirm", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 5)));
    expect(stream.applyRealtime(realtime(5, false, { close: "101" }))).toEqual({ ok: true, inserted: 1, replaced: 0 });
    expect(stream.applyRealtime(realtime(5, false, { close: "102" }))).toEqual({ ok: true, inserted: 0, replaced: 1 });
    expect(stream.applyRealtime(realtime(5, true, { close: "102" }))).toEqual({ ok: true, inserted: 0, replaced: 1 });
    const snap = stream.snapshot();
    expect(indices(snap)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(snap.candles[5]).toMatchObject({ confirmed: true, record: { close: "102" } });
  });

  it("inserts out-of-order realtime candles in time order", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyRealtime(realtime(3, true));
    stream.applyRealtime(realtime(1, true));
    stream.applyRealtime(realtime(2, true));
    expect(indices(stream.snapshot())).toEqual([1, 2, 3]);
  });

  it("never regresses a confirmed candle: forming or conflicting updates are rejected, identical is a no-op", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 3)));
    expect(stream.applyRealtime(realtime(1, false, { close: "50" }))).toMatchObject({
      ok: false,
      rejection: { code: "confirmed_regression", openTime: iso(T0 + H) },
    });
    expect(stream.applyRealtime(realtime(1, true, { close: "50" }))).toMatchObject({
      ok: false,
      rejection: { code: "confirmed_regression" },
    });
    expect(stream.applyRealtime(realtime(1, false))).toEqual({ ok: true, inserted: 0, replaced: 0 });
    expect(stream.snapshot().candles[1]!.record.close).toBe("105");
    expect(stream.snapshot().rejections.map((r) => r.code)).toEqual(["confirmed_regression", "confirmed_regression"]);
  });

  it("rejects a page that would regress a confirmed realtime candle without partial merge", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyRealtime(realtime(4, true, { close: "111" }));
    const result = stream.applyPage(page([candle(2), candle(3), candle(4, { close: "222" })]));
    expect(result).toMatchObject({ ok: false, rejection: { code: "confirmed_regression" } });
    expect(indices(stream.snapshot())).toEqual([4]);
  });
});

describe("candleStream gaps (fail-closed, no interpolation)", () => {
  it("exposes server gaps as markers and inserts no synthetic candles", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page([candle(0), candle(1), candle(4), candle(5)], { gaps: [[2, 4]] }));
    const snap = stream.snapshot();
    expect(indices(snap)).toEqual([0, 1, 4, 5]);
    expect(snap.gaps).toEqual([{ startMs: T0 + 2 * H, endMs: T0 + 4 * H, source: "server" }]);
  });

  it("marks the never-loaded range between history and realtime, and clears it once loaded", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page(range(0, 3)));
    stream.applyRealtime(realtime(6, false));
    expect(stream.snapshot().gaps).toEqual([{ startMs: T0 + 3 * H, endMs: T0 + 6 * H, source: "uncovered" }]);
    stream.applyPage(page(range(3, 6)));
    expect(stream.snapshot().gaps).toEqual([]);
    expect(indices(stream.snapshot())).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it("does not duplicate the same server gap across pages", () => {
    const stream = createCandleStream({ key: KEY });
    stream.applyPage(page([candle(0), candle(3)], { gaps: [[1, 3]] }));
    stream.applyPage(page([candle(0), candle(3)], { gaps: [[1, 3]] }));
    expect(stream.snapshot().gaps).toHaveLength(1);
  });
});

describe("candleStream negative inputs", () => {
  it("rejects parser failures, key/adjustment mismatches and foreign candle keys", () => {
    const stream = createCandleStream({ key: KEY, adjustment: "RAW" });
    const other: SeriesKey = { ...KEY, venue: "KIS_KRX" };
    expect(stream.applyPage({ kind: "invalid" })).toMatchObject({ ok: false, rejection: { code: "parse_invalid" } });
    expect(stream.applyPage({ kind: "unsupported_schema_version", received: "v2" })).toMatchObject({
      ok: false,
      rejection: { code: "unsupported_schema_version", detail: "received v2" },
    });
    expect(stream.applyPage(page(range(0, 1), { key: other }))).toMatchObject({
      ok: false,
      rejection: { code: "key_mismatch" },
    });
    expect(stream.applyPage(page(range(0, 1), { adjustment: "ADJUSTED" }))).toMatchObject({
      ok: false,
      rejection: { code: "key_mismatch" },
    });
    expect(stream.applyPage(page([candle(0), candle(1, { key: other })]))).toMatchObject({
      ok: false,
      rejection: { code: "key_mismatch", openTime: iso(T0 + H) },
    });
    expect(stream.applyRealtime({ candle: candle(0, { key: other }), confirmed: true })).toMatchObject({
      ok: false,
      rejection: { code: "key_mismatch" },
    });
    const snap = stream.snapshot();
    expect(snap.candles).toEqual([]);
    expect(snap.rejections).toHaveLength(6);
  });

  it("rejects unusable or non-chronological timestamps without a partial merge", () => {
    const stream = createCandleStream({ key: KEY });
    const bad = candle(2, { close_time: candle(2).open_time });
    expect(stream.applyPage(page([candle(0), candle(1), bad]))).toMatchObject({
      ok: false,
      rejection: { code: "invalid_time", openTime: bad.open_time },
    });
    expect(stream.snapshot().candles).toEqual([]);
    expect(stream.applyRealtime({ candle: candle(0, { open_time: "garbage" }), confirmed: false })).toMatchObject({
      ok: false,
      rejection: { code: "invalid_time" },
    });
  });

  it("caps retained rejections at maxRejections (oldest dropped)", () => {
    const stream = createCandleStream({ key: KEY, maxRejections: 2 });
    stream.applyPage({ kind: "invalid" });
    stream.applyPage({ kind: "unsupported_schema_version", received: null });
    stream.applyPage({ kind: "invalid" });
    expect(stream.snapshot().rejections.map((r) => r.code)).toEqual(["unsupported_schema_version", "parse_invalid"]);
  });
});

describe("candleStream source injection and lifecycle", () => {
  function fakeSource() {
    const state = { key: null as SeriesKey | null, handler: null as ((u: RealtimeCandleUpdate) => void) | null, unsubs: 0 };
    const source: RealtimeCandleSource = {
      subscribe(key, onUpdate) {
        state.key = key;
        state.handler = onUpdate;
        return () => {
          state.unsubs += 1;
        };
      },
    };
    return { state, source };
  }

  it("connects with the stream key, forwards updates, and unsubscribes on dispose", () => {
    const stream = createCandleStream({ key: KEY });
    const { state, source } = fakeSource();
    const seen: number[] = [];
    stream.subscribe((snap) => seen.push(snap.candles.length));
    stream.connect(source);
    expect(state.key).toEqual(KEY);
    state.handler!(realtime(0, false));
    state.handler!(realtime(1, false));
    expect(indices(stream.snapshot())).toEqual([0, 1]);
    expect(seen).toEqual([1, 2]);
    stream.dispose();
    expect(state.unsubs).toBe(1);
    expect(stream.applyRealtime(realtime(2, true))).toMatchObject({ ok: false, rejection: { code: "disposed" } });
    expect(stream.applyPage(page(range(0, 1)))).toMatchObject({ ok: false, rejection: { code: "disposed" } });
    expect(() => stream.connect(source)).toThrow(/disposed/);
    stream.dispose();
    expect(state.unsubs).toBe(1);
  });

  it("reconnecting releases the previous source subscription", () => {
    const stream = createCandleStream({ key: KEY });
    const first = fakeSource();
    const second = fakeSource();
    stream.connect(first.source);
    stream.connect(second.source);
    expect(first.state.unsubs).toBe(1);
    expect(second.state.unsubs).toBe(0);
  });

  it("notifies listeners on rejections too, and reset clears all state", () => {
    const stream = createCandleStream({ key: KEY });
    const codes: Array<number> = [];
    const unsubscribe = stream.subscribe((snap) => codes.push(snap.rejections.length));
    stream.applyPage(page(range(0, 2)), "c1");
    stream.applyPage({ kind: "invalid" });
    expect(codes).toEqual([0, 1]);
    stream.reset();
    expect(stream.snapshot()).toMatchObject({ candles: [], gaps: [], nextCursor: null, rejections: [] });
    unsubscribe();
    stream.applyPage(page(range(0, 1)));
    expect(codes).toEqual([0, 1, 0]);
  });
});

// DEEPEN(task-3074) of task-1523 (CH-2, commit df59201), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had 19 passing tests
// with strong negative-path coverage and seeded-shuffle determinism, but the
// only "failure injection" was fakeSource — a domain stub, not a
// network/transport failure simulation — and there was no numeric
// performance assertion and no gate-red reproduction. candleStream is pure
// and synchronous with no fetch/DB of its own, so the closest analogue to a
// network/DB failure here is a misbehaving RealtimeCandleSource or listener —
// the one boundary this module actually has with the outside world.
describe("candleStream — DEEPEN(task-3074): failure injection, numeric perf, gate-red, D3", () => {
  describe("실패 주입 (transport/listener failures, not domain stubs)", () => {
    it("propagates a source.subscribe() failure out of connect() instead of swallowing it", () => {
      const stream = createCandleStream({ key: KEY });
      const failingSource: RealtimeCandleSource = {
        subscribe() {
          throw new Error("transport: handshake refused");
        },
      };
      expect(() => stream.connect(failingSource)).toThrow(/handshake refused/);
    });

    it("rejects a zombie update delivered by the source after dispose instead of applying it", () => {
      const stream = createCandleStream({ key: KEY });
      let capturedHandler: ((u: RealtimeCandleUpdate) => void) | null = null;
      const zombieSource: RealtimeCandleSource = {
        subscribe(_key, onUpdate) {
          capturedHandler = onUpdate;
          return () => {
            // simulates a transport bug: it keeps delivering after teardown/unsubscribe
          };
        },
      };
      stream.connect(zombieSource);
      stream.dispose();

      capturedHandler!(realtime(0, true)); // zombie delivery racing the teardown

      expect(stream.snapshot().candles).toEqual([]);
      expect(stream.applyRealtime(realtime(0, true))).toMatchObject({ ok: false, rejection: { code: "disposed" } });
    });

    it("does not swallow a throwing listener: the exception propagates out of applyPage instead of being silently dropped", () => {
      const stream = createCandleStream({ key: KEY });
      stream.subscribe(() => {
        throw new Error("consumer: render failed");
      });
      expect(() => stream.applyPage(page(range(0, 1)))).toThrow(/render failed/);
    });
  });

  describe("수치 성능", () => {
    it("merges 8,000 candles across 20 out-of-order pages within a 1500ms budget", () => {
      const stream = createCandleStream({ key: KEY });
      const pages = Array.from({ length: 20 }, (_, i) => range(i * 400, i * 400 + 400));
      const startedAt = performance.now();
      for (const p of shuffled(pages, 7)) {
        const result = stream.applyPage(page(p));
        if (!result.ok) throw new Error(`unexpected rejection: ${result.rejection.code}`);
      }
      const elapsedMs = performance.now() - startedAt;
      expect(stream.snapshot().candles).toHaveLength(8_000);
      expect(elapsedMs).toBeLessThan(1500);
    });

    it("applies 3,000 strictly-descending (worst-case front-insert) realtime updates within a 2000ms budget", () => {
      const stream = createCandleStream({ key: KEY });
      const startedAt = performance.now();
      for (let i = 3000; i > 0; i -= 1) {
        stream.applyRealtime(realtime(i, true));
      }
      const elapsedMs = performance.now() - startedAt;
      expect(stream.snapshot().candles).toHaveLength(3000);
      expect(elapsedMs).toBeLessThan(2000);
    });
  });

  describe("게이트 적색 재현 (two-phase validate-then-commit atomicity, at scale)", () => {
    it("a duplicate_conflict buried in the middle of a 60-candle page rejects the whole page — zero partial application", () => {
      const stream = createCandleStream({ key: KEY });
      const records = range(0, 60);
      records.splice(41, 0, candle(30, { close: "999" })); // conflicts with the candle(30) already staged at position 30
      const result = stream.applyPage(page(records));
      expect(result).toMatchObject({ ok: false, rejection: { code: "duplicate_conflict" } });
      // if phase-1 staging and phase-2 commit were collapsed into a single pass, the 40
      // candles preceding the conflict would already be committed by the time it is found
      expect(stream.snapshot().candles).toEqual([]);
    });

    it("a confirmed_regression triggered mid-page rejects the whole page — none of the other 59 candles are committed", () => {
      const stream = createCandleStream({ key: KEY });
      stream.applyRealtime(realtime(30, true, { close: "111" }));
      const records = range(0, 60).map((r) => (r.open_time === candle(30).open_time ? candle(30, { close: "222" }) : r));
      const result = stream.applyPage(page(records));
      expect(result).toMatchObject({ ok: false, rejection: { code: "confirmed_regression" } });
      // a single-pass merge would have already inserted indices 0-29 before reaching the conflict at 30
      expect(indices(stream.snapshot())).toEqual([30]);
    });
  });

  describe("어드버서리얼 (D3): multi-instance isolation and deterministic replay", () => {
    it("two independently connected streams never cross-deliver realtime updates or share dispose/unsubscribe state", () => {
      const streamA = createCandleStream({ key: KEY });
      const otherKey: SeriesKey = { ...KEY, instrument_id: "i-2" };
      const streamB = createCandleStream({ key: otherKey });
      let handlerA: ((u: RealtimeCandleUpdate) => void) | null = null;
      let handlerB: ((u: RealtimeCandleUpdate) => void) | null = null;
      let unsubsA = 0;
      let unsubsB = 0;
      streamA.connect({
        subscribe(_key, onUpdate) {
          handlerA = onUpdate;
          return () => {
            unsubsA += 1;
          };
        },
      });
      streamB.connect({
        subscribe(_key, onUpdate) {
          handlerB = onUpdate;
          return () => {
            unsubsB += 1;
          };
        },
      });

      handlerA!(realtime(0, true));
      handlerB!(realtime(1, true, { key: otherKey }));
      expect(indices(streamA.snapshot())).toEqual([0]);
      expect(indices(streamB.snapshot())).toEqual([1]);

      streamA.dispose();
      expect(unsubsA).toBe(1);
      expect(unsubsB).toBe(0);
      expect(streamB.applyRealtime(realtime(2, true, { key: otherKey }))).toEqual({ ok: true, inserted: 1, replaced: 0 });
      expect(indices(streamB.snapshot())).toEqual([1, 2]);
    });

    it("replaying the same 20-page sequence through two independent fresh streams reaches identical snapshots", () => {
      const pages = Array.from({ length: 20 }, (_, i) => range(i * 10, i * 10 + 10));
      const streamA = createCandleStream({ key: KEY });
      const streamB = createCandleStream({ key: KEY });
      for (const p of pages) streamA.applyPage(page(p));
      for (const p of pages) streamB.applyPage(page(p));
      expect(streamB.snapshot()).toEqual(streamA.snapshot());
    });
  });
});
