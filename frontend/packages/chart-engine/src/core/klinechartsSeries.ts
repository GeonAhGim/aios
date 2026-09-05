/**
 * Series-level delegation to the vendored klinecharts chart (CH-1b).
 *
 * klinecharts v10 is pull-based: once symbol + period + loader are set the
 * chart asks the `DataLoader` for bars and receives live bars through
 * `subscribeBar`. Our `SeriesBackend` contract is push-based (setData/update),
 * so each binding keeps its own snapshot and answers the vendor's pulls from it:
 *  - "candlestick" feeds the main candle pane through a DataLoader.
 *  - "line" / "histogram" are mapped onto klinecharts indicators whose `calc`
 *    looks up the series' values by timestamp. Line series draw on the candle
 *    pane, histogram series get their own pane. The plotted value is `close`.
 * A binding may be created before the chart is mounted; `attach()` replays it.
 */

import {
  type DataLoader,
  getSupportedIndicators,
  type Indicator,
  type IndicatorTemplate,
  type KLineData,
  PaneIdConstants,
  registerIndicator,
  type VendorChart,
} from "./klinecharts";
import { type CandlePoint, type SeriesBackend, type SeriesOptions, sortedInsertOrReplace } from "./series";

export const AIOS_LINE_INDICATOR = "AIOS_SERIES_LINE";
export const AIOS_HISTOGRAM_INDICATOR = "AIOS_SERIES_HISTOGRAM";

const DEFAULT_SYMBOL = { ticker: "AIOS" };
const DEFAULT_PERIOD = { type: "minute", span: 1 } as const;

export interface VendorSeriesBinding extends SeriesBackend {
  attach(chart: VendorChart): void;
  detach(): void;
}

export function toKLineData(point: CandlePoint): KLineData {
  const { time, open, high, low, close, volume } = point;
  const bar: KLineData = { timestamp: time, open, high, low, close };
  if (volume !== undefined) bar.volume = volume;
  return bar;
}

export function createCandleBinding(): VendorSeriesBinding {
  let points: readonly CandlePoint[] = [];
  let chart: VendorChart | null = null;
  let pushLiveBar: ((bar: KLineData) => void) | null = null;

  const loader: DataLoader = {
    getBars(params) {
      // 'init' answers with the full snapshot. 'forward'/'backward' pagination
      // is CH-2 (candleStream) scope; until then there is never more to load.
      params.callback(params.type === "init" ? points.map(toKLineData) : [], false);
    },
    subscribeBar(params) {
      pushLiveBar = params.callback;
    },
    unsubscribeBar() {
      pushLiveBar = null;
    },
  };

  function reload(): void {
    chart?.resetData();
  }

  return {
    attach(next) {
      chart = next;
      chart.setSymbol(DEFAULT_SYMBOL);
      chart.setPeriod(DEFAULT_PERIOD);
      chart.setDataLoader(loader); // triggers the vendor's 'init' pull
    },
    detach() {
      pushLiveBar = null;
      chart = null;
    },
    setData(next) {
      points = [...next];
      reload();
    },
    update(point) {
      const last = points[points.length - 1];
      const touchesTail = last === undefined || point.time >= last.time;
      points = sortedInsertOrReplace(points, point);
      if (touchesTail && pushLiveBar !== null) {
        pushLiveBar(toKLineData(point));
        return;
      }
      // The vendor's single-bar path only appends or replaces the last bar;
      // anything earlier needs a full replay to stay consistent.
      reload();
    },
    remove() {
      points = [];
      reload();
    },
  };
}

interface SeriesValueRow {
  value?: number;
}

type ValueSourceKey = string;
const valueSources = new Map<ValueSourceKey, ReadonlyMap<number, number>>();
let bindingSeq = 0;

function calcFromValueSource(dataList: KLineData[], indicator: Indicator<SeriesValueRow, string | number>): SeriesValueRow[] {
  const source = valueSources.get(String(indicator.calcParams[0]));
  return dataList.map((bar) => ({ value: source?.get(bar.timestamp) }));
}

const LINE_TEMPLATE: IndicatorTemplate<SeriesValueRow, string | number> = {
  name: AIOS_LINE_INDICATOR,
  shortName: "",
  series: "price",
  figures: [{ key: "value", type: "line" }],
  calc: calcFromValueSource,
};

const HISTOGRAM_TEMPLATE: IndicatorTemplate<SeriesValueRow, string | number> = {
  name: AIOS_HISTOGRAM_INDICATOR,
  shortName: "",
  series: "normal",
  figures: [{ key: "value", type: "bar", baseValue: 0 }],
  calc: calcFromValueSource,
};

function ensureTemplatesRegistered(): void {
  const supported = new Set(getSupportedIndicators());
  if (!supported.has(AIOS_LINE_INDICATOR)) registerIndicator(LINE_TEMPLATE);
  if (!supported.has(AIOS_HISTOGRAM_INDICATOR)) registerIndicator(HISTOGRAM_TEMPLATE);
}

export function createIndicatorBinding(options: SeriesOptions): VendorSeriesBinding {
  ensureTemplatesRegistered();
  const isLine = options.type === "line";
  const name = isLine ? AIOS_LINE_INDICATOR : AIOS_HISTOGRAM_INDICATOR;
  bindingSeq += 1;
  const sourceKey: ValueSourceKey = `${options.id}#${bindingSeq}`;
  let values = new Map<number, number>();
  valueSources.set(sourceKey, values);
  let chart: VendorChart | null = null;
  let indicatorId: string | null = null;
  let revision = 0;

  function recalc(): void {
    if (chart === null || indicatorId === null) return;
    // Changing calcParams is the vendor's documented way to force `calc` to re-run.
    revision += 1;
    chart.overrideIndicator({ id: indicatorId, name, calcParams: [sourceKey, revision] });
  }

  return {
    attach(next) {
      chart = next;
      indicatorId = chart.createIndicator(
        {
          name,
          calcParams: [sourceKey, revision],
          ...(isLine ? { paneId: PaneIdConstants.CANDLE } : {}),
        },
        true, // stack: never evict other indicators sharing the pane
      );
    },
    detach() {
      chart = null;
      indicatorId = null;
    },
    setData(points) {
      values = new Map(points.map((p) => [p.time, p.close]));
      valueSources.set(sourceKey, values);
      recalc();
    },
    update(point) {
      values.set(point.time, point.close);
      recalc();
    },
    remove() {
      if (chart !== null && indicatorId !== null) chart.removeIndicator({ id: indicatorId });
      indicatorId = null;
      valueSources.delete(sourceKey);
    },
  };
}

export function createVendorSeriesBinding(options: SeriesOptions): VendorSeriesBinding {
  return options.type === "candlestick" ? createCandleBinding() : createIndicatorBinding(options);
}
