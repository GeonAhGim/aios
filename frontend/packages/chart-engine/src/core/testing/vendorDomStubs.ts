/**
 * jsdom lacks the browser surface klinecharts touches at init: ResizeObserver,
 * window.matchMedia, a 2D canvas context, and layout (clientWidth/clientHeight
 * are always 0). These stubs are just enough for the vendor to lay out panes
 * and hold data; nothing is rasterised. Test support only, not exported from
 * the package index.
 */

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function matchMediaStub(query: string): MediaQueryList {
  return {
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}

function createContext2dStub(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const gradient = { addColorStop(): void {} };
  const noop = (): undefined => undefined;
  const handler: ProxyHandler<object> = {
    get(_target, prop) {
      if (prop === "canvas") return canvas;
      if (prop === "measureText") {
        return (text: string) => ({ width: text.length * 7, actualBoundingBoxAscent: 7, actualBoundingBoxDescent: 2 });
      }
      if (prop === "createLinearGradient" || prop === "createRadialGradient") return () => gradient;
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 });
      return noop;
    },
    set() {
      return true;
    },
  };
  return new Proxy({}, handler) as CanvasRenderingContext2D;
}

function pxOf(el: HTMLElement, prop: "width" | "height"): number | null {
  const match = /^(\d+(?:\.\d+)?)px$/.exec(el.style[prop]);
  return match ? Number(match[1]) : null;
}

/** Explicit px size wins; otherwise inherit the nearest sized ancestor (mimics a 100% width). */
function stubbedSize(el: HTMLElement, prop: "width" | "height"): number {
  const own = pxOf(el, prop);
  if (own !== null) return own;
  return el.parentElement ? stubbedSize(el.parentElement, prop) : 0;
}

/** Installs the stubs and returns a function that restores the previous globals. */
export function installVendorDomStubs(): () => void {
  const globals = globalThis as { ResizeObserver?: unknown };
  const previous = {
    resizeObserver: globals.ResizeObserver,
    matchMedia: window.matchMedia,
    getContext: HTMLCanvasElement.prototype.getContext,
    clientWidth: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth"),
    clientHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight"),
  };

  globals.ResizeObserver = ResizeObserverStub;
  window.matchMedia = matchMediaStub;
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement, contextId: string) {
    return contextId === "2d" ? createContext2dStub(this) : null;
  } as typeof HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return stubbedSize(this, "width");
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return stubbedSize(this, "height");
    },
  });

  return () => {
    globals.ResizeObserver = previous.resizeObserver;
    window.matchMedia = previous.matchMedia;
    HTMLCanvasElement.prototype.getContext = previous.getContext;
    const sizes: Array<["clientWidth" | "clientHeight", PropertyDescriptor | undefined]> = [
      ["clientWidth", previous.clientWidth],
      ["clientHeight", previous.clientHeight],
    ];
    for (const [name, descriptor] of sizes) {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name];
    }
  };
}
