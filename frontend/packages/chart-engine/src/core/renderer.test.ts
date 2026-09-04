import { describe, expect, it } from "vitest";
import { createNullRendererBackend, createRenderer } from "./renderer";

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
