/**
 * Canvas-agnostic render surface contract. `RendererBackend` is the seam CH-1b
 * (task-1501) fills with a klinecharts-backed implementation — until then,
 * `createRenderer()` defaults to a no-op backend so the contract is testable
 * without vendor code.
 */

export interface RenderSize {
  readonly width: number;
  readonly height: number;
}

export interface RendererBackend {
  mount(container: HTMLElement | null): void;
  unmount(): void;
  resize(size: RenderSize): void;
  requestRender(): void;
  dispose(): void;
}

export interface Renderer {
  readonly size: RenderSize;
  readonly renderRequestCount: number;
  mount(container: HTMLElement | null): void;
  unmount(): void;
  resize(size: RenderSize): void;
  requestRender(): void;
  dispose(): void;
}

export interface CreateRendererOptions {
  backend?: RendererBackend;
  initialSize?: RenderSize;
}

function assertValidSize(size: RenderSize): void {
  if (size.width < 0 || size.height < 0) {
    throw new RangeError(`RenderSize must be non-negative, got ${size.width}x${size.height}`);
  }
}

class NullRendererBackend implements RendererBackend {
  mount(): void {}
  unmount(): void {}
  resize(): void {}
  requestRender(): void {}
  dispose(): void {}
}

export function createNullRendererBackend(): RendererBackend {
  return new NullRendererBackend();
}

export function createRenderer(options: CreateRendererOptions = {}): Renderer {
  const backend = options.backend ?? createNullRendererBackend();
  let size = options.initialSize ?? { width: 0, height: 0 };
  assertValidSize(size);
  let renderRequestCount = 0;
  let disposed = false;

  function assertNotDisposed(): void {
    if (disposed) throw new Error("Renderer is disposed");
  }

  return {
    get size() {
      return size;
    },
    get renderRequestCount() {
      return renderRequestCount;
    },
    mount(container) {
      assertNotDisposed();
      backend.mount(container);
    },
    unmount() {
      assertNotDisposed();
      backend.unmount();
    },
    resize(next) {
      assertNotDisposed();
      assertValidSize(next);
      size = next;
      backend.resize(next);
    },
    requestRender() {
      assertNotDisposed();
      renderRequestCount += 1;
      backend.requestRender();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      backend.dispose();
    },
  };
}
