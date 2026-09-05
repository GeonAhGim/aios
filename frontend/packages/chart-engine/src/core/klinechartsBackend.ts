/**
 * Chart-level delegation to the vendored klinecharts fork (CH-1b). One
 * `KlinechartsBackend` owns one vendor `Chart` and hands the wrapper layer the
 * four seams it needs: RendererBackend, SeriesBackendFactory, TimeScaleBackend
 * and PriceScaleBackend. Vendor DOM/canvas code only runs after `mount()`;
 * series created earlier are replayed onto the chart when it appears.
 */

import { disposeVendorChart, initVendorChart, PaneIdConstants, type VendorChart } from "./klinecharts";
import { createVendorSeriesBinding, type VendorSeriesBinding } from "./klinechartsSeries";
import { createPriceScale, type PriceScaleBackend } from "./priceScale";
import { createRenderer, type RendererBackend, type RenderSize } from "./renderer";
import { type ChartEngine, createChartEngine, type SeriesBackendFactory, type SeriesOptions } from "./series";
import { createTimeScale, type TimeScaleBackend } from "./timeScale";

export interface KlinechartsBackend {
  /** Live vendor chart, or null while unmounted. CH-3/CH-4 reach vendor overlay APIs through this. */
  readonly chart: VendorChart | null;
  readonly renderer: RendererBackend;
  readonly seriesBackendFactory: SeriesBackendFactory;
  readonly timeScale: TimeScaleBackend;
  readonly priceScale: PriceScaleBackend;
}

const CANDLE_PANE = { paneId: PaneIdConstants.CANDLE };

function finiteOrNull(value: number | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function first<T>(value: T | T[]): T | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function createKlinechartsBackend(): KlinechartsBackend {
  let chart: VendorChart | null = null;
  let container: HTMLElement | null = null;
  const bindings = new Set<VendorSeriesBinding>();

  function unmount(): void {
    if (chart === null) return;
    for (const binding of bindings) binding.detach();
    disposeVendorChart(chart);
    // The vendor leaves its id attribute behind; drop it so the container is reusable.
    container?.removeAttribute("k-line-chart-id");
    chart = null;
    container = null;
  }

  /** Vendor coordinate conversion is meaningless before any bar exists. */
  function mountedWithData(): VendorChart | null {
    return chart !== null && chart.getDataList().length > 0 ? chart : null;
  }

  const renderer: RendererBackend = {
    mount(next) {
      if (next === null) {
        unmount();
        return;
      }
      if (chart !== null) {
        if (container === next) return;
        unmount();
      }
      const created = initVendorChart(next);
      if (created === null) throw new Error("klinecharts refused to initialise on the given container");
      chart = created;
      container = next;
      for (const binding of bindings) binding.attach(created);
    },
    unmount,
    resize(size: RenderSize) {
      if (container === null) return;
      container.style.width = `${size.width}px`;
      container.style.height = `${size.height}px`;
      chart?.resize();
    },
    requestRender() {
      // The vendor store invalidates and redraws itself on every mutation
      // (data, indicators, layout); there is no separate frame to request.
    },
    dispose: unmount,
  };

  const seriesBackendFactory: SeriesBackendFactory = (options: SeriesOptions) => {
    const binding = createVendorSeriesBinding(options);
    bindings.add(binding);
    if (chart !== null) binding.attach(chart);
    return {
      setData: (points) => binding.setData(points),
      update: (point) => binding.update(point),
      remove() {
        binding.remove();
        binding.detach();
        bindings.delete(binding);
      },
    };
  };

  const timeScale: TimeScaleBackend = {
    timeToX(time) {
      const live = mountedWithData();
      if (live === null) return null;
      return finiteOrNull(first(live.convertToPixel({ timestamp: time }, CANDLE_PANE))?.x);
    },
    xToTime(x) {
      const live = mountedWithData();
      if (live === null) return null;
      return finiteOrNull(first(live.convertFromPixel([{ x }], CANDLE_PANE))?.timestamp);
    },
  };

  const priceScale: PriceScaleBackend = {
    priceToY(price) {
      const live = mountedWithData();
      if (live === null) return null;
      return finiteOrNull(first(live.convertToPixel({ value: price }, CANDLE_PANE))?.y);
    },
    yToPrice(y) {
      const live = mountedWithData();
      if (live === null) return null;
      return finiteOrNull(first(live.convertFromPixel([{ y }], CANDLE_PANE))?.value);
    },
  };

  return {
    get chart() {
      return chart;
    },
    renderer,
    seriesBackendFactory,
    timeScale,
    priceScale,
  };
}

export interface CreateKlinechartsChartEngineOptions {
  /** Mount immediately when given. `renderer.mount()` can also be called later. */
  container?: HTMLElement | null;
  initialSize?: RenderSize;
}

export interface KlinechartsChartEngine extends ChartEngine {
  readonly vendor: KlinechartsBackend;
}

/** A `ChartEngine` whose renderer, scales and series all delegate to one vendor chart. */
export function createKlinechartsChartEngine(options: CreateKlinechartsChartEngineOptions = {}): KlinechartsChartEngine {
  const vendor = createKlinechartsBackend();
  const initialSize = options.initialSize ?? { width: 0, height: 0 };
  const renderer = createRenderer({ backend: vendor.renderer, initialSize });
  const engine = createChartEngine({
    renderer,
    timeScale: createTimeScale({ backend: vendor.timeScale, width: initialSize.width }),
    priceScale: createPriceScale({ backend: vendor.priceScale, height: initialSize.height }),
    seriesBackendFactory: vendor.seriesBackendFactory,
  });
  if (options.container) {
    renderer.mount(options.container);
    if (initialSize.width > 0 || initialSize.height > 0) engine.resize(initialSize);
  }
  return { ...engine, vendor };
}
