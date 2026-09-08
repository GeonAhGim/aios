export type { StatusLineCandle, StatusLineLegend, StatusLineNeighbor, StatusLineOptions } from "../legend/statusLine";
export { buildStatusLineLegends } from "../legend/statusLine"; // statusLineStyle.ts (vendor-typed) deliberately not re-exported — see dataWindowStyle.ts note below.

export type {
  DataWindowErrorCode,
  DataWindowOptions,
  DataWindowRow,
  IndicatorFigureSource,
  IndicatorSeriesSnapshot,
} from "../legend/dataWindow";
export { DataWindowError, computeDataWindowRows } from "../legend/dataWindow";

// dataWindowStyle.ts (vendor-typed) is deliberately not re-exported here —
// this barrel is imported by apps/web, and that file's vendor types break
// apps/web's tsc -b (see dataWindowStyle.ts docstring). Import it directly
// from chart-engine-internal code only.

export type {
  IndicatorSource,
  ObjectKind,
  ObjectTreeEntry,
  ObjectTreeErrorCode,
  ObjectTreeSource,
  ObjectTreeState,
  ObjectTreeStateErrorCode,
  OverlaySource,
} from "../legend/objectTree";
export {
  ObjectTreeError,
  ObjectTreeStateError,
  applyObjectTreeState,
  buildObjectTree,
  encodeObjectTreeState,
  moveEntry,
  setEntryLocked,
  setEntryVisible,
} from "../legend/objectTree";
