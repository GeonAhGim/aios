/**
 * CH-16 — dataWindow: `computeDataWindowRows`, the one piece of real logic
 * needed beyond the vendor's own on-chart indicator tooltip (which draws
 * `indicator.result[dataIndex]` for a single indicator at a time internally
 * — see `dataWindowStyle.ts` for the style binding onto that vendor view).
 * What a "data window" needs beyond the on-chart tooltip is a *simultaneous,
 * all-indicators* row list (DoD: "지표 30종 값 동시 표시") that a non-canvas
 * panel can render — `computeDataWindowRows` is that pure projection,
 * decoupled from any vendor `Indicator` instance so it is testable without a
 * live chart.
 *
 * No vendor import here on purpose (type boundary, see `dataWindowStyle.ts`
 * docstring): this file is the half of CH-16 dataWindow that apps/web's
 * screen (`DataWindowPanel.tsx`) actually consumes, and apps/web's
 * verbatimModuleSyntax/erasableSyntaxOnly tsconfig breaks `tsc -b` the
 * moment a file it reaches types anything out of `vendor/klinecharts` (the
 * vendor source isn't authored against those flags). Keeping this module
 * vendor-free is what lets apps/web import it at all.
 *
 * Fail-closed: two indicator snapshots sharing an id is a caller bug (the
 * backend `IndicatorRegistry` — CH-3 `overlayRegistry.ts` — guarantees
 * unique ids), so it is rejected via `DataWindowError` rather than silently
 * keeping the last one and dropping the other's values.
 */

export interface IndicatorFigureSource {
  readonly key: string;
  readonly title: string;
  readonly color?: string;
}

/** One indicator's current calculation state, decoupled from the vendor `Indicator` shape. */
export interface IndicatorSeriesSnapshot {
  readonly id: string;
  readonly precision?: number;
  readonly figures: readonly IndicatorFigureSource[];
  /** Indexed identically to the candle series (`result[dataIndex]`), same convention as vendor `Indicator.result`. */
  readonly result: readonly (Readonly<Record<string, number | undefined>> | undefined)[];
}

export interface DataWindowRow {
  readonly indicatorId: string;
  readonly outputKey: string;
  readonly label: string;
  readonly value: string;
  readonly color: string;
}

export interface DataWindowOptions {
  readonly defaultValue?: string;
  readonly defaultColor?: string;
  readonly defaultPrecision?: number;
}

export type DataWindowErrorCode = "CHART_DATA_WINDOW_DUPLICATE_INDICATOR";

export class DataWindowError extends Error {
  readonly code: DataWindowErrorCode;

  constructor(code: DataWindowErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "DataWindowError";
    this.code = code;
  }
}

const DEFAULT_VALUE = "n/a";
const DEFAULT_COLOR = "#76808F";
const DEFAULT_PRECISION = 4;

function assertUniqueIndicatorIds(indicators: readonly IndicatorSeriesSnapshot[]): void {
  const seen = new Set<string>();
  for (const indicator of indicators) {
    if (seen.has(indicator.id)) {
      throw new DataWindowError("CHART_DATA_WINDOW_DUPLICATE_INDICATOR", `duplicate indicator id "${indicator.id}"`);
    }
    seen.add(indicator.id);
  }
}

/**
 * Projects every indicator's value at `dataIndex` (the crosshair-resolved
 * bar) into a flat row list. An out-of-range `dataIndex` (before the first
 * bar, past the last, or an indicator whose `result` hasn't caught up yet)
 * is not an error — every figure just renders `defaultValue`, matching how
 * the vendor tooltip treats a missing point.
 */
export function computeDataWindowRows(
  indicators: readonly IndicatorSeriesSnapshot[],
  dataIndex: number,
  options: DataWindowOptions = {},
): readonly DataWindowRow[] {
  assertUniqueIndicatorIds(indicators);
  const defaultValue = options.defaultValue ?? DEFAULT_VALUE;
  const defaultColor = options.defaultColor ?? DEFAULT_COLOR;

  const rows: DataWindowRow[] = [];
  for (const indicator of indicators) {
    const precision = indicator.precision ?? options.defaultPrecision ?? DEFAULT_PRECISION;
    const point = dataIndex >= 0 && dataIndex < indicator.result.length ? indicator.result[dataIndex] : undefined;
    for (const figure of indicator.figures) {
      const raw = point?.[figure.key];
      const value = typeof raw === "number" && Number.isFinite(raw) ? raw.toFixed(precision) : defaultValue;
      rows.push({
        indicatorId: indicator.id,
        outputKey: figure.key,
        label: figure.title,
        value,
        color: figure.color ?? defaultColor,
      });
    }
  }
  return rows;
}
