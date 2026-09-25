import { describe, expect, it } from "vitest";
import { createNullRendererBackend, createRenderer, type RendererBackend } from "./renderer";

describe("createRenderer", () => {
  it("starts at the given initial size and forwards resize to the backend", () => {
    const resized: Array<{ width: number; height: number }> = [];
    const backend = {
      ...createNullRendererBackend(),
      resize(size: { width: number; height: number }) {
        resized.push(size);
      },
    };
    const renderer = createRenderer({ backend, initialSize: { width: 100, height: 50 } });

    expect(renderer.size).toEqual({ width: 100, height: 50 });

    renderer.resize({ width: 800, height: 600 });

    expect(renderer.size).toEqual({ width: 800, height: 600 });
    expect(resized).toEqual([{ width: 800, height: 600 }]);
  });

  it("rejects negative dimensions on construction and on resize", () => {
    expect(() => createRenderer({ initialSize: { width: -1, height: 10 } })).toThrow(RangeError);
    const renderer = createRenderer();
    expect(() => renderer.resize({ width: 10, height: -1 })).toThrow(RangeError);
  });

  it("counts render requests and delegates them to the backend", () => {
    let backendRequests = 0;
    const backend = { ...createNullRendererBackend(), requestRender: () => { backendRequests += 1; } };
    const renderer = createRenderer({ backend });

    renderer.requestRender();
    renderer.requestRender();

    expect(renderer.renderRequestCount).toBe(2);
    expect(backendRequests).toBe(2);
  });

  it("disposes idempotently and rejects use-after-dispose", () => {
    let disposeCalls = 0;
    const backend = { ...createNullRendererBackend(), dispose: () => { disposeCalls += 1; } };
    const renderer = createRenderer({ backend });

    renderer.dispose();
    renderer.dispose();

    expect(disposeCalls).toBe(1);
    expect(() => renderer.requestRender()).toThrow(/disposed/);
    expect(() => renderer.resize({ width: 1, height: 1 })).toThrow(/disposed/);
  });
});

// DEEPEN(task-3069) of task-1375 (CH-1a, commit 87da8d1), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had 4 tests here with
// negative-path coverage (RangeError, disposed-after-use) but no failure
// injection, no numeric performance assertion, no gate-red reproduction, and
// no D3-level proof. This block fills those four gaps without changing
// renderer.ts's behavior.
describe("createRenderer — DEEPEN(task-3069): failure injection, perf, gate-red, D3", () => {
  it("실패 주입: a backend that throws on mount() propagates the error and leaves the renderer usable for dispose() afterwards", () => {
    const backend: RendererBackend = {
      ...createNullRendererBackend(),
      mount() {
        throw new Error("canvas context unavailable");
      },
    };
    const renderer = createRenderer({ backend });

    expect(() => renderer.mount(null)).toThrow(/canvas context unavailable/);
    // A crash inside the vendor backend during mount must not corrupt the
    // renderer's own disposed/undisposed bookkeeping.
    expect(() => renderer.dispose()).not.toThrow();
  });

  it("실패 주입: a backend that throws on resize() propagates the error (not swallowed) and does not touch renderRequestCount", () => {
    const backend: RendererBackend = {
      ...createNullRendererBackend(),
      resize() {
        throw new Error("vendor resize failed: detached canvas");
      },
    };
    const renderer = createRenderer({ backend, initialSize: { width: 10, height: 10 } });

    expect(() => renderer.resize({ width: 20, height: 20 })).toThrow(/detached canvas/);
    expect(renderer.renderRequestCount).toBe(0);
  });

  it("수치 성능: 50,000 requestRender calls stay under a 600ms budget and renderRequestCount is exact", () => {
    const renderer = createRenderer();
    const startedAt = performance.now();
    for (let i = 0; i < 50_000; i++) renderer.requestRender();
    const elapsedMs = performance.now() - startedAt;

    expect(renderer.renderRequestCount).toBe(50_000);
    expect(elapsedMs).toBeLessThan(600);
  });

  it("게이트 적색 재현: calling dispose() 100 times only ever invokes backend.dispose() once — deleting the `if (disposed) return` guard in renderer.ts would make disposeCalls come out as 100 and fail this assertion", () => {
    let disposeCalls = 0;
    const backend = { ...createNullRendererBackend(), dispose: () => { disposeCalls += 1; } };
    const renderer = createRenderer({ backend });

    for (let i = 0; i < 100; i++) renderer.dispose();

    expect(disposeCalls).toBe(1);
  });

  it("다중 인스턴스(D3): two renderers backed by independent backends never cross-contaminate size, render counts, or disposal", () => {
    const backendA = { ...createNullRendererBackend(), disposeCalls: 0 };
    backendA.dispose = () => { backendA.disposeCalls += 1; };
    const backendB = { ...createNullRendererBackend(), disposeCalls: 0 };
    backendB.dispose = () => { backendB.disposeCalls += 1; };

    const rendererA = createRenderer({ backend: backendA, initialSize: { width: 100, height: 100 } });
    const rendererB = createRenderer({ backend: backendB, initialSize: { width: 200, height: 200 } });

    rendererA.resize({ width: 50, height: 50 });
    rendererA.requestRender();
    rendererA.requestRender();
    rendererB.requestRender();
    rendererA.dispose();

    expect(rendererA.size).toEqual({ width: 50, height: 50 });
    expect(rendererB.size).toEqual({ width: 200, height: 200 });
    expect(rendererA.renderRequestCount).toBe(2);
    expect(rendererB.renderRequestCount).toBe(1);
    expect(backendA.disposeCalls).toBe(1);
    expect(backendB.disposeCalls).toBe(0);
    expect(() => rendererB.requestRender()).not.toThrow();
  });
});
