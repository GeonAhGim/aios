/**
 * CH-8 — immutable multi-chart layout + watchlist model ("layout-v1").
 *
 * This is the shape the server calls `layout_state` (§9.6 CH-5,
 * `src/foundation/charting/domain/models.py`): an opaque `dict[str, Any]`
 * that the backend never validates. `encodeLayoutModel`/`decodeLayoutModel`
 * are the frontend-owned contract for that dict, mirrored on CH-4's
 * `serialize.ts` fail-closed style (unknown/missing/wrong-typed field →
 * typed error, never silently dropped or coerced). The codec itself lives in
 * `layoutModelCodec.ts` and is re-exported below (P6 300-line split, pure
 * move) — this module keeps the model shape + error type only.
 *
 * A panel does not embed drawings — `drawingSetId` is a reference. The
 * actual `DrawingCollection` for a layout is fetched/saved separately via
 * `persistence.ts` + CH-4 `serialize.ts` (one drawing set per layout,
 * `ports/repository.py` `get_drawing_set`/`put_drawings`) and is not
 * reinvented here.
 *
 * No renderer/vendor dependency: pure TypeScript, consumed by persistence.ts
 * and by chart-shell composition code in apps/web.
 */

export const LAYOUT_MODEL_SCHEMA_VERSION = 1;

export interface InstrumentRef {
  readonly instrumentId: string;
  readonly venue: string;
  readonly symbol: string;
}

export interface IndicatorRef {
  readonly id: string;
  readonly params?: Readonly<Record<string, number>>;
}

export interface ChartPanel {
  readonly id: string;
  readonly instrument: InstrumentRef;
  readonly timeframe: string;
  readonly indicators: readonly IndicatorRef[];
  /** Reference to the layout's CH-4 drawing set — never embedded here. */
  readonly drawingSetId: string;
  /**
   * CH-16b — persisted display order for this panel's legend/object tree
   * (`legend/objectTree.ts` `ObjectTreeEntry.id`s, indicators and drawings
   * mixed). Applied leniently via `sortByPersistedOrder`, never required to
   * cover every current object 1:1 (missing/optional — absent means "natural
   * order", not an error). Not the same axis as `indicators`' own array
   * order, which only ever reflects selection order.
   */
  readonly objectTreeOrder?: readonly string[];
  /**
   * CH-16b — indicator ids locked via the legend (`legend/objectTree.ts`
   * `lockedIndicatorIds` parameter). Drawings/overlays already carry their
   * own native `locked` field (CH-4 `drawings/model.ts`) and are not
   * duplicated here.
   */
  readonly lockedIndicatorIds?: readonly string[];
}

export type WatchlistEntry = InstrumentRef;

export interface Watchlist {
  readonly id: string;
  readonly name: string;
  readonly entries: readonly WatchlistEntry[];
}

export interface ChartLayoutModel {
  readonly schemaVersion: typeof LAYOUT_MODEL_SCHEMA_VERSION;
  readonly panels: readonly ChartPanel[];
  /** Must be `null` or the `id` of one of `panels`. */
  readonly activePanelId: string | null;
  readonly watchlists: readonly Watchlist[];
}

export function createEmptyLayoutModel(): ChartLayoutModel {
  return { schemaVersion: LAYOUT_MODEL_SCHEMA_VERSION, panels: [], activePanelId: null, watchlists: [] };
}

export type LayoutModelErrorCode =
  | "CHART_LAYOUT_SCHEMA_UNSUPPORTED"
  | "CHART_LAYOUT_FIELD_MISSING"
  | "CHART_LAYOUT_FIELD_UNKNOWN"
  | "CHART_LAYOUT_FIELD_INVALID"
  | "CHART_LAYOUT_DUPLICATE_ID";

export class LayoutModelError extends Error {
  readonly code: LayoutModelErrorCode;

  constructor(code: LayoutModelErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "LayoutModelError";
    this.code = code;
  }
}

export { decodeLayoutModel, encodeLayoutModel } from "./layoutModelCodec";
