/**
 * Series lifecycle (create/update/remove) and the top-level `ChartEngine`
 * that composes renderer + timeScale + priceScale + series. `SeriesBackend`
 * is the CH-1b delegation seam, same shape as RendererBackend: a klinecharts
 * series wrapper slots in later without changing this contract.
 */

import { createPriceScale, type PriceScale } from "./priceScale";
import { createRenderer, type RenderSize, type Renderer } from "./renderer";
import { createTimeScale, type TimeScale } from "./timeScale";

export type SeriesType = "candlestick" | "line" | "histogram";

export interface CandlePoint {
  readonly time: number;
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
  readonly volume?: number;
}

export interface SeriesOptions {
  readonly id: string;
  readonly type: SeriesType;
}

export interface SeriesBackend {
  setData(points: readonly CandlePoint[]): void;
  update(point: CandlePoint): void;
  remove(): void;
}

export type SeriesBackendFactory = (options: SeriesOptions) => SeriesBackend;

export function createNullSeriesBackendFactory(): SeriesBackendFactory {
  return () => ({
    setData(): void {},
    update(): void {},
    remove(): void {},
  });
}

export interface SeriesHandle {
  readonly id: string;
  readonly type: SeriesType;
  readonly data: readonly CandlePoint[];
  setData(points: readonly CandlePoint[]): void;
  update(point: CandlePoint): void;
}

export interface ChartEngine {
  readonly renderer: Renderer;
  readonly timeScale: TimeScale;
  readonly priceScale: PriceScale;
  createSeries(options: SeriesOptions): SeriesHandle;
  removeSeries(id: string): void;
  getSeries(id: string): SeriesHandle | undefined;
  resize(size: RenderSize): void;
  dispose(): void;
}

export interface CreateChartEngineOptions {
  renderer?: Renderer;
  timeScale?: TimeScale;
  priceScale?: PriceScale;
  seriesBackendFactory?: SeriesBackendFactory;
}

function sortedInsertOrReplace(data: readonly CandlePoint[], point: CandlePoint): CandlePoint[] {
  const idx = data.findIndex((p) => p.time === point.time);
  if (idx >= 0) {
    const next = data.slice();
    next[idx] = point;
    return next;
  }
  const insertAt = data.findIndex((p) => p.time > point.time);
  if (insertAt < 0) return [...data, point];
  return [...data.slice(0, insertAt), point, ...data.slice(insertAt)];
}

export function createChartEngine(options: CreateChartEngineOptions = {}): ChartEngine {
  const renderer = options.renderer ?? createRenderer();
  const timeScale = options.timeScale ?? createTimeScale();
  const priceScale = options.priceScale ?? createPriceScale();
  const seriesBackendFactory = options.seriesBackendFactory ?? createNullSeriesBackendFactory();

  const series = new Map<string, { handle: SeriesHandle; backend: SeriesBackend }>();
  let disposed = false;

  function assertNotDisposed(): void {
    if (disposed) throw new Error("ChartEngine is disposed");
  }

  function createSeries(seriesOptions: SeriesOptions): SeriesHandle {
    assertNotDisposed();
    if (series.has(seriesOptions.id)) {
      throw new Error(`Series "${seriesOptions.id}" already exists`);
    }
    const backend = seriesBackendFactory(seriesOptions);
    let data: readonly CandlePoint[] = [];
    const handle: SeriesHandle = {
      id: seriesOptions.id,
      type: seriesOptions.type,
      get data() {
        return data;
      },
      setData(points) {
        data = [...points].sort((a, b) => a.time - b.time);
        backend.setData(data);
        renderer.requestRender();
      },
      update(point) {
        data = sortedInsertOrReplace(data, point);
        backend.update(point);
        renderer.requestRender();
      },
    };
    series.set(seriesOptions.id, { handle, backend });
    renderer.requestRender();
    return handle;
  }

  function removeSeries(id: string): void {
    const entry = series.get(id);
    if (!entry) return;
    entry.backend.remove();
    series.delete(id);
    renderer.requestRender();
  }

  function getSeries(id: string): SeriesHandle | undefined {
    return series.get(id)?.handle;
  }

  function resize(size: RenderSize): void {
    assertNotDisposed();
    renderer.resize(size);
    timeScale.setWidth(size.width);
    priceScale.setHeight(size.height);
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    for (const id of [...series.keys()]) removeSeries(id);
    renderer.dispose();
  }

  return {
    renderer,
    timeScale,
    priceScale,
    createSeries,
    removeSeries,
    getSeries,
    resize,
    dispose,
  };
}
