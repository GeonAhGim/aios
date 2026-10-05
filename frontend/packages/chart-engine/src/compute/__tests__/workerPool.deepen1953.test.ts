import { describe, expect, it, vi } from "vitest";

import type { IndicatorCatalogPort } from "../../plugins/indicatorPlugin";
import { loadIndicatorCatalog } from "../../plugins/indicatorPlugin";
import { CLIENT_ENGINE_COMPUTE_TASK, ClientEngineError, KERNEL_FACTORIES, computeIndicatorSeries, createClientIncrementalIndicator } from "../clientEngine";
import { VERIFIED_KERNEL_PINS } from "../verifiedIndicators";
import { createInlineWorkerPoolBackend, createWorkerPool } from "../workerPool";

// --- DEEPEN 1953 (docs/audit/DEPTH_CH.md): 원 리프는 워커 콜백 강제 throw
// 2건만 "준-실패주입"으로 인정됐을 뿐 mocked network/DB 실패주입, 수치 성능
// 단언, 게이트 적색 재현이 전부 없었다. 아래 세 축을 test-only로 보강한다.
// (D3는 CH가 안전축 목록에 없어 D2 하한이라 이 리프의 대상이 아니다.)

describe("DEEPEN 1953 — mocked network failure injection (real IndicatorCatalogPort boundary)", () => {
  it("a network failure loading the catalog (IndicatorCatalogPort.listIndicators rejects) fails the whole compute path closed, wrapped by the pool as WORKER_POOL_TASK_FAILED", async () => {
    // `IndicatorCatalogPort` is the actual `@aios/api-client` network boundary
    // this module's catalog ultimately comes from (IND-12 GET /v1/indicators)
    // — unlike the worker-callback throw already covered above, this models a
    // real network/DB-style failure (timeout, 5xx, connection refused), not a
    // synchronous handler exception.
    const listIndicators = vi.fn().mockRejectedValue(new Error("ECONNREFUSED: indicators service unreachable"));
    const port: IndicatorCatalogPort = { listIndicators };

    const loaded = await loadIndicatorCatalog(port);
    expect(loaded.kind).toBe("error");
    // fail-closed: a network failure yields `null`, never a stale/cached catalog.
    const catalog = loaded.kind === "ok" ? loaded.page.items : null;

    const backend = createInlineWorkerPoolBackend({ [CLIENT_ENGINE_COMPUTE_TASK]: computeIndicatorSeries });
    const pool = createWorkerPool([backend]);

    await expect(
      pool.submit(CLIENT_ENGINE_COMPUTE_TASK, {
        name: "SMA",
        params: { timeperiod: 2 },
        bars: [{ close: 1 }, { close: 2 }, { close: 3 }],
        catalog,
      }),
    ).rejects.toMatchObject({
      code: "WORKER_POOL_TASK_FAILED",
      message: expect.stringContaining("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED"),
    });
    expect(listIndicators).toHaveBeenCalledTimes(1);
    pool.dispose();
  });
});

describe("DEEPEN 1953 — numeric performance assertion", () => {
  it("computes a 50,000-bar SMA series through the pool within a 2000ms budget", async () => {
    const catalog = [
      {
        name: "SMA",
        tier: VERIFIED_KERNEL_PINS.SMA!.tier,
        category: "test",
        version: "ind-v1",
        hash: VERIFIED_KERNEL_PINS.SMA!.entryHash,
        inputs: ["close"],
        outputs: ["value"],
      },
    ];
    const bars = Array.from({ length: 50_000 }, (_, i) => ({ close: 100 + Math.sin(i / 50) * 10 }));
    const backend = createInlineWorkerPoolBackend({ [CLIENT_ENGINE_COMPUTE_TASK]: computeIndicatorSeries });
    const pool = createWorkerPool([backend]);

    const start = performance.now();
    const series = await pool.submit<
      { name: string; params: Record<string, number>; bars: typeof bars; catalog: typeof catalog },
      Record<string, ReadonlyArray<number | null>>
    >(CLIENT_ENGINE_COMPUTE_TASK, { name: "SMA", params: { timeperiod: 20 }, bars, catalog });
    const elapsedMs = performance.now() - start;

    expect(series.value).toHaveLength(50_000);
    expect(series.value?.[49_999]).not.toBeNull();
    expect(elapsedMs).toBeLessThan(2000);
    pool.dispose();
  });
});

describe("DEEPEN 1953 — gate red reproduction (naive kernel bypass vs the real whitelist gate)", () => {
  it("적색: KERNEL_FACTORIES를 화이트리스트 없이 직접 호출하면 entry_hash 드리프트를 무시하고 계산을 계속한다; 녹색: createClientIncrementalIndicator는 같은 드리프트를 즉시 거부한다", () => {
    const driftedCatalog = [
      {
        name: "SMA",
        tier: VERIFIED_KERNEL_PINS.SMA!.tier,
        category: "test",
        version: "ind-v1",
        hash: "0".repeat(64), // drifted away from VERIFIED_KERNEL_PINS.SMA.entryHash
        inputs: ["close"],
        outputs: ["value"],
      },
    ];

    // Red: a hypothetical regression that reaches straight into KERNEL_FACTORIES
    // (bypassing createClientIncrementalIndicator's catalog check) has no idea
    // the server's canonical spec drifted — it silently keeps computing.
    const bypassed = KERNEL_FACTORIES.SMA!({ timeperiod: 2 }, "SMA");
    expect(bypassed.update({ close: 1 })).toBeNull(); // window not full yet
    expect(bypassed.update({ close: 3 })).toEqual([2]); // computed anyway — no gate consulted

    // Green: the real, only sanctioned entry point refuses the exact same
    // drifted catalog instead of computing a value nobody re-verified.
    expect(() => createClientIncrementalIndicator("SMA", { timeperiod: 2 }, driftedCatalog)).toThrow(ClientEngineError);
    try {
      createClientIncrementalIndicator("SMA", { timeperiod: 2 }, driftedCatalog);
      throw new Error("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ClientEngineError);
      expect((err as ClientEngineError).code).toBe("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED");
    }
  });
});
