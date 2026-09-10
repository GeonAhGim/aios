// DEEPEN(task-3068) of task-1133 (CH-0, commit b0826da), per DEPTH_CH 감사
// (task-2729, docs/audit/DEPTH_CH.md): 원 리프는 "pass/fail 단언 없이 JSON
// 리포트만 출력"하는 스크립트 하나뿐이라 유닛테스트 파일 0건 · negative-path
// 0건 · 실패 주입 0건 · 게이트 적색 재현 0건으로 D2 축 하한 미달(D0) 판정을
// 받았다. 이 파일이 그 4가지를 채운다. bench_chart_candles.mjs 자체의 CLI
// 동작(esbuild/puppeteer-core를 caller cwd에서 동적 resolve)은 건드리지
// 않았다 — benchOne에 테스트 전용 `deps` 주입 지점(loadEsbuild/loadPuppeteer/
// resolveChromePath, 전부 기본값 = 기존 실동작)만 추가했다.
import { describe, expect, it, vi } from "vitest";
import {
  ENTRIES,
  benchOne,
  findChrome,
  genCandles,
  main,
  mulberry32,
  percentile,
} from "./bench_chart_candles.mjs";

describe("mulberry32 (seeded PRNG backing genCandles determinism)", () => {
  it("is fully deterministic for a fixed seed", () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    const seqA = Array.from({ length: 5 }, () => a());
    const seqB = Array.from({ length: 5 }, () => b());
    expect(seqA).toEqual(seqB);
  });

  it("negative-path: different seeds diverge (a stuck/constant PRNG would fail this)", () => {
    const a = mulberry32(1);
    const b = mulberry32(2);
    expect(a()).not.toBeCloseTo(b(), 6);
  });

  it("always returns a value in [0, 1)", () => {
    const rnd = mulberry32(7);
    for (let i = 0; i < 1000; i++) {
      const v = rnd();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});

describe("genCandles (deterministic synthetic OHLCV)", () => {
  it("negative-path: n=0 returns an empty array, not undefined/throw", () => {
    expect(genCandles(0)).toEqual([]);
  });

  it("게이트 적색 재현(결정론 회귀): seed=42, n=1 is pinned to today's exact mulberry32 output — swapping the PRNG for Math.random() or reseeding would silently break §4's 'no fabricated numbers, only reproducible measurements' claim and this test would go red", () => {
    const [c] = genCandles(1, 42);
    expect(c.time).toBe(1700000000);
    expect(c.open).toBe(100);
    expect(c.close).toBeCloseTo(100.060_662_25, 6);
    expect(c.high).toBeCloseTo(100.239_978_47, 6);
    expect(c.low).toBeCloseTo(99.659_013_68, 6);
    expect(c.volume).toBeCloseTo(344.867_020_72, 6);
  });

  it("is byte-for-byte reproducible across two independent calls with the same seed", () => {
    expect(genCandles(500, 42)).toEqual(genCandles(500, 42));
  });

  it("holds OHLC invariants and the 0.01 price floor across 5,000 candles (drift can be negative)", () => {
    const candles = genCandles(5000, 42);
    expect(candles).toHaveLength(5000);
    for (const c of candles) {
      expect(c.high).toBeGreaterThanOrEqual(Math.max(c.open, c.close));
      expect(c.low).toBeLessThanOrEqual(Math.min(c.open, c.close));
      expect(c.close).toBeGreaterThanOrEqual(0.01);
      expect(c.volume).toBeGreaterThanOrEqual(10);
      expect(c.volume).toBeLessThan(510);
    }
    // 60s bar spacing must be exact and monotonic (klinecharts/lightweight-charts both
    // key their time axis off this) — a single off-by-one in the loop would show up here.
    for (let i = 1; i < candles.length; i++) {
      expect(candles[i].time - candles[i - 1].time).toBe(60);
    }
  });

  it("수치 성능 단언: generating 100,000 candles (the §4 bench scale) stays well under a 2s budget", () => {
    const t0 = performance.now();
    const candles = genCandles(100_000, 42);
    const elapsedMs = performance.now() - t0;
    expect(candles).toHaveLength(100_000);
    expect(elapsedMs).toBeLessThan(2000);
  });
});

describe("percentile (nearest-rank, used for panZoomFrameMsP50/P95 in §4.2)", () => {
  it("computes p50/p95 by nearest-rank (floor(p/100*n)), not linear interpolation", () => {
    // 10 sorted samples: nearest-rank p50 -> index floor(0.5*10)=5 -> value 60
    // (a linear-interpolation percentile — the other common definition — would give
    // 55 here instead; pinning 60 is the 게이트 적색 재현 for this axis: swapping the
    // algorithm silently changes every fps number in the eval doc's §4.2 table).
    const samples = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    expect(percentile(samples, 50)).toBe(60);
    expect(percentile(samples, 95)).toBe(100);
  });

  it("negative-path: unsorted input is sorted internally before ranking", () => {
    expect(percentile([100, 10, 50, 20], 50)).toBe(50);
  });

  it("negative-path: single-element array returns that element for any p", () => {
    expect(percentile([42], 0)).toBe(42);
    expect(percentile([42], 99)).toBe(42);
  });

  it("negative-path: empty array does not throw, degrades to NaN (documents the real, unguarded edge case)", () => {
    expect(percentile([], 50)).toBeNaN();
  });
});

describe("findChrome (Chrome/Edge discovery for the headless bench run)", () => {
  it("CHROME_PATH env override takes priority over the hardcoded candidate list", () => {
    const prev = process.env.CHROME_PATH;
    process.env.CHROME_PATH = "Z:/definitely/not/a/real/browser.exe";
    try {
      expect(findChrome()).toBe("Z:/definitely/not/a/real/browser.exe");
    } finally {
      if (prev === undefined) delete process.env.CHROME_PATH;
      else process.env.CHROME_PATH = prev;
    }
  });
});

describe("benchOne failure injection (forced loader/browser failures, deps-injected)", () => {
  it("실패 주입: puppeteer-core resolves but browser.launch() rejects (real headless-Chrome crash mode) -> UNMEASURED, never throws", async () => {
    const deps = {
      loadEsbuild: async () => ({ default: { build: async () => ({ outputFiles: [{ text: "void 0" }] }) } }),
      loadPuppeteer: async () => ({
        default: { launch: vi.fn(async () => { throw new Error("Failed to launch the browser process"); }) },
      }),
      resolveChromePath: () => "C:/fake/chrome.exe",
    };
    const result = await benchOne("klinecharts", genCandles(10), 2, deps);
    expect(result.status).toBe("UNMEASURED");
    expect(result.reason).toMatch(/Failed to launch the browser process/);
  });

  it("실패 주입 2: the candidate's injected runBench script throws inside the page (evaluate rejects) -> UNMEASURED and browser.close() still runs (finally)", async () => {
    const page = {
      setViewport: vi.fn(async () => {}),
      setContent: vi.fn(async () => {}),
      addScriptTag: vi.fn(async () => {}),
      waitForFunction: vi.fn(async () => {}),
      evaluate: vi.fn(async () => { throw new Error("ReferenceError: klinecharts is not defined"); }),
    };
    const close = vi.fn(async () => {});
    const deps = {
      loadEsbuild: async () => ({ default: { build: async () => ({ outputFiles: [{ text: "void 0" }] }) } }),
      loadPuppeteer: async () => ({
        default: { launch: vi.fn(async () => ({ newPage: vi.fn(async () => page), close })) },
      }),
      resolveChromePath: () => "C:/fake/chrome.exe",
    };
    const result = await benchOne("klinecharts", genCandles(10), 2, deps);
    expect(result.status).toBe("UNMEASURED");
    expect(result.reason).toMatch(/klinecharts is not defined/);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("negative-path: no Chrome/Edge resolvable -> UNMEASURED with an actionable reason, esbuild/puppeteer never touched further", async () => {
    const deps = {
      loadEsbuild: async () => ({ default: { build: vi.fn() } }),
      loadPuppeteer: async () => ({ default: { launch: vi.fn() } }),
      resolveChromePath: () => null,
    };
    const result = await benchOne("klinecharts", genCandles(10), 2, deps);
    expect(result).toEqual({ status: "UNMEASURED", reason: "no local Chrome/Edge executable found (set CHROME_PATH)" });
  });

  it("happy path (deps fully faked): MEASURED result rounds initialRenderMs and derives p50/p95 via percentile()", async () => {
    const page = {
      setViewport: vi.fn(async () => {}),
      setContent: vi.fn(async () => {}),
      addScriptTag: vi.fn(async () => {}),
      waitForFunction: vi.fn(async () => {}),
      evaluate: vi.fn(async () => ({
        initialRenderMs: 123.456,
        frameTimesMs: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        callTimesMs: [1, 2],
      })),
    };
    const deps = {
      loadEsbuild: async () => ({ default: { build: async () => ({ outputFiles: [{ text: "void 0" }] }) } }),
      loadPuppeteer: async () => ({
        default: { launch: vi.fn(async () => ({ newPage: vi.fn(async () => page), close: vi.fn(async () => {}) })) },
      }),
      resolveChromePath: () => "C:/fake/chrome.exe",
    };
    const result = await benchOne("klinecharts", genCandles(10), 10, deps);
    expect(result.status).toBe("MEASURED");
    expect(result.candleCount).toBe(10);
    expect(result.initialRenderMs).toBe(123.46);
    expect(result.panZoomFrameMsP50).toBe(60);
    expect(result.panZoomFrameMsP95).toBe(100);
  });
});

describe("main() CLI entrypoint", () => {
  it("negative-path: unknown --lib value is reported as UNMEASURED without attempting any resolution", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const prevArgv = process.argv;
    process.argv = ["node", "bench_chart_candles.mjs", "--lib=not-a-real-candidate", "--candles=1", "--steps=1"];
    try {
      const results = await main();
      expect(results["not-a-real-candidate"]).toEqual({ status: "UNMEASURED", reason: "unknown candidate 'not-a-real-candidate'" });
    } finally {
      process.argv = prevArgv;
      logSpy.mockRestore();
    }
  });

  it("게이트 적색 재현: run for real (no injected deps) against this repo's actual state — every candidate is UNMEASURED today because puppeteer-core is not installed anywhere in the monorepo (by design: CH-1 vendoring hasn't happened for this bench script's own deps). If a CI gate ever required MEASURED here, it would be red right now, exactly as §4.1's documented graceful-degradation contract promises.", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const prevArgv = process.argv;
    process.argv = ["node", "bench_chart_candles.mjs", "--lib=all", "--candles=5", "--steps=1"];
    try {
      const results = await main();
      expect(Object.keys(results).sort()).toEqual(Object.keys(ENTRIES).sort());
      for (const lib of Object.keys(ENTRIES)) {
        expect(results[lib].status).toBe("UNMEASURED");
        expect(results[lib].reason).toMatch(/puppeteer-core not installed/);
      }
    } finally {
      process.argv = prevArgv;
      logSpy.mockRestore();
    }
  });
});
