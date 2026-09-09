import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_INDICATOR_WORKER_POOL_SIZE, createIndicatorComputePool } from "./indicatorComputePool";

/** Minimal `Worker`-shaped double — same shape as workerPool.test.ts's FakeWorker. */
class FakeWorker {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminate = vi.fn();
  postMessage = vi.fn();
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createIndicatorComputePool", () => {
  // CH-18e DoD: fail-closed — no Worker global (SSR, old browser, jsdom without a
  // stub) must return null rather than throw, so callers fall back to main-thread compute.
  it("negative: Worker가 없는 환경(jsdom 기본값)에서는 null을 반환한다", () => {
    expect(typeof Worker).toBe("undefined");
    expect(createIndicatorComputePool()).toBeNull();
  });

  it("Worker가 있으면 기본 풀 크기만큼 워커를 만들어 풀을 반환한다", () => {
    const created: FakeWorker[] = [];
    vi.stubGlobal(
      "Worker",
      function FakeWorkerCtor(this: FakeWorker) {
        Object.assign(this, new FakeWorker());
        created.push(this);
      },
    );

    const pool = createIndicatorComputePool();

    expect(pool).not.toBeNull();
    expect(pool?.size).toBe(DEFAULT_INDICATOR_WORKER_POOL_SIZE);
    expect(created).toHaveLength(DEFAULT_INDICATOR_WORKER_POOL_SIZE);
    pool?.dispose();
  });

  it("poolSize를 넘기면 그 개수만큼 워커를 만든다", () => {
    const created: FakeWorker[] = [];
    vi.stubGlobal(
      "Worker",
      function FakeWorkerCtor(this: FakeWorker) {
        Object.assign(this, new FakeWorker());
        created.push(this);
      },
    );

    const pool = createIndicatorComputePool(2);

    expect(pool?.size).toBe(2);
    expect(created).toHaveLength(2);
    pool?.dispose();
  });

  it("dispose하면 만들어진 워커 전부 terminate된다", () => {
    const created: FakeWorker[] = [];
    vi.stubGlobal(
      "Worker",
      function FakeWorkerCtor(this: FakeWorker) {
        Object.assign(this, new FakeWorker());
        created.push(this);
      },
    );

    const pool = createIndicatorComputePool(2);
    pool?.dispose();

    expect(created.every((w) => w.terminate.mock.calls.length === 1)).toBe(true);
  });
});
