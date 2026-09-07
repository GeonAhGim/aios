/**
 * CH-16 — objectTree: the "지표 트리" inventory, and the only genuinely new
 * implementation in this leaf (§9.11 CH-16 table: statusLine/dataWindow are
 * style bindings; this is `getOverlays()`/`getIndicators()` inventory).
 *
 * `ObjectTreeSource` is a narrow structural subset of vendor `Chart`
 * (`core/klinecharts.ts` `VendorChart`) — any object exposing those two
 * getters satisfies it, so a live chart can be passed directly with no
 * adapter, while tests use plain fakes without touching canvas/DOM.
 *
 * Ordering and visibility are vendor-owned (`zLevel`/`visible`, mutated via
 * `overrideIndicator`/`overrideOverlay` — not reimplemented here). Locking
 * is not: vendor `Overlay.lock` exists but `Indicator` has no lock field at
 * all, so this module is the source of truth for "is this indicator locked"
 * (`lockedIndicatorIds`) and folds it together with the overlay's native
 * `lock` into one uniform `ObjectTreeEntry.locked`.
 *
 * Persistence is a plain id-list round trip (`encodeObjectTreeState` /
 * `applyObjectTreeState`), the same fail-closed shape as CH-8
 * `layoutModel.ts`: a persisted state that doesn't cover every current
 * object 1:1 is rejected rather than silently re-ordered or truncated.
 */

export type ObjectKind = "indicator" | "overlay";

export interface ObjectTreeEntry {
  readonly id: string;
  readonly kind: ObjectKind;
  readonly paneId: string;
  readonly name: string;
  readonly visible: boolean;
  readonly locked: boolean;
}

export interface IndicatorSource {
  readonly id: string;
  readonly paneId: string;
  readonly name: string;
  readonly visible: boolean;
  readonly zLevel: number;
}

export interface OverlaySource {
  readonly id: string;
  readonly paneId: string;
  readonly name: string;
  readonly visible: boolean;
  readonly zLevel: number;
  readonly lock: boolean;
}

/** Structurally satisfied by vendor `VendorChart` (`getIndicators()`/`getOverlays()`) — no import of it needed here. */
export interface ObjectTreeSource {
  getIndicators(): readonly IndicatorSource[];
  getOverlays(): readonly OverlaySource[];
}

export type ObjectTreeErrorCode =
  | "CHART_OBJECT_TREE_DUPLICATE_ID"
  | "CHART_OBJECT_TREE_NOT_FOUND"
  | "CHART_OBJECT_TREE_INVALID_INDEX";

export class ObjectTreeError extends Error {
  readonly code: ObjectTreeErrorCode;

  constructor(code: ObjectTreeErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ObjectTreeError";
    this.code = code;
  }
}

interface RankedEntry extends ObjectTreeEntry {
  readonly zLevel: number;
}

function assertUniqueIds(entries: readonly RankedEntry[]): void {
  const seen = new Set<string>();
  for (const entry of entries) {
    if (seen.has(entry.id)) {
      throw new ObjectTreeError("CHART_OBJECT_TREE_DUPLICATE_ID", `duplicate object id "${entry.id}" across indicators/overlays`);
    }
    seen.add(entry.id);
  }
}

/**
 * Combined, orderable inventory: indicators and overlays share one id/order
 * space, sorted by vendor draw order (`zLevel`) ascending. `lockedIndicatorIds`
 * layers this module's own lock state onto indicators (vendor has none);
 * overlays use their native `lock` field.
 */
export function buildObjectTree(
  source: ObjectTreeSource,
  lockedIndicatorIds: ReadonlySet<string> = new Set(),
): readonly ObjectTreeEntry[] {
  const indicatorEntries: RankedEntry[] = source.getIndicators().map((indicator) => ({
    id: indicator.id,
    kind: "indicator",
    paneId: indicator.paneId,
    name: indicator.name,
    visible: indicator.visible,
    locked: lockedIndicatorIds.has(indicator.id),
    zLevel: indicator.zLevel,
  }));
  const overlayEntries: RankedEntry[] = source.getOverlays().map((overlay) => ({
    id: overlay.id,
    kind: "overlay",
    paneId: overlay.paneId,
    name: overlay.name,
    visible: overlay.visible,
    locked: overlay.lock,
    zLevel: overlay.zLevel,
  }));

  const combined = [...indicatorEntries, ...overlayEntries];
  assertUniqueIds(combined);
  return combined
    .slice()
    .sort((a, b) => a.zLevel - b.zLevel)
    .map(({ zLevel: _zLevel, ...entry }) => entry);
}

function findIndex(entries: readonly ObjectTreeEntry[], id: string): number {
  const index = entries.findIndex((entry) => entry.id === id);
  if (index < 0) throw new ObjectTreeError("CHART_OBJECT_TREE_NOT_FOUND", `no object with id "${id}"`);
  return index;
}

/** Moves an entry to `toIndex`, shifting the others — the tree's own persisted "order" (independent of vendor `zLevel`). */
export function moveEntry(entries: readonly ObjectTreeEntry[], id: string, toIndex: number): readonly ObjectTreeEntry[] {
  const fromIndex = findIndex(entries, id);
  if (!Number.isInteger(toIndex) || toIndex < 0 || toIndex >= entries.length) {
    throw new ObjectTreeError("CHART_OBJECT_TREE_INVALID_INDEX", `toIndex must be in [0, ${entries.length}), got ${toIndex}`);
  }
  const next = entries.slice();
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved!);
  return next;
}

export function setEntryVisible(entries: readonly ObjectTreeEntry[], id: string, visible: boolean): readonly ObjectTreeEntry[] {
  const index = findIndex(entries, id);
  const next = entries.slice();
  next[index] = { ...next[index]!, visible };
  return next;
}

export function setEntryLocked(entries: readonly ObjectTreeEntry[], id: string, locked: boolean): readonly ObjectTreeEntry[] {
  const index = findIndex(entries, id);
  const next = entries.slice();
  next[index] = { ...next[index]!, locked };
  return next;
}

/** JSON-safe persisted shape: id order plus the hidden/locked subsets. */
export interface ObjectTreeState {
  readonly order: readonly string[];
  readonly hidden: readonly string[];
  readonly locked: readonly string[];
}

export function encodeObjectTreeState(entries: readonly ObjectTreeEntry[]): ObjectTreeState {
  return {
    order: entries.map((entry) => entry.id),
    hidden: entries.filter((entry) => !entry.visible).map((entry) => entry.id),
    locked: entries.filter((entry) => entry.locked).map((entry) => entry.id),
  };
}

export type ObjectTreeStateErrorCode = "CHART_OBJECT_TREE_STATE_UNKNOWN_ID" | "CHART_OBJECT_TREE_STATE_COVERAGE_MISMATCH";

export class ObjectTreeStateError extends Error {
  readonly code: ObjectTreeStateErrorCode;

  constructor(code: ObjectTreeStateErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ObjectTreeStateError";
    this.code = code;
  }
}

/**
 * Re-applies a persisted order/visible/locked state onto a freshly built
 * inventory (e.g. after reload). Every id in `state.order` must match the
 * current inventory 1:1 — a stale persisted state (an indicator removed or
 * added since save) is rejected rather than silently dropped or reordered
 * around the mismatch, so a caller always knows to re-derive/migrate it.
 */
export function applyObjectTreeState(
  entries: readonly ObjectTreeEntry[],
  state: ObjectTreeState,
): readonly ObjectTreeEntry[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry] as const));
  for (const id of state.order) {
    if (!byId.has(id)) {
      throw new ObjectTreeStateError("CHART_OBJECT_TREE_STATE_UNKNOWN_ID", `persisted order references unknown id "${id}"`);
    }
  }
  if (state.order.length !== entries.length) {
    throw new ObjectTreeStateError(
      "CHART_OBJECT_TREE_STATE_COVERAGE_MISMATCH",
      `persisted order covers ${state.order.length} objects, expected ${entries.length}`,
    );
  }
  const hidden = new Set(state.hidden);
  const locked = new Set(state.locked);
  return state.order.map((id) => {
    const entry = byId.get(id)!;
    return { ...entry, visible: !hidden.has(id), locked: locked.has(id) };
  });
}

/**
 * CH-16b — best-effort display reorder for a persisted `order` (e.g.
 * `ChartPanel.objectTreeOrder`, saved via `encodeObjectTreeState(...).order`
 * after a `moveEntry` call). Unlike `applyObjectTreeState`, this never throws
 * on a coverage mismatch: entries the saved order doesn't cover — a new
 * indicator selected since the order was last saved, one whose drawing was
 * deleted — are perfectly ordinary here (indicators/drawings come and go far
 * more often than whole saved layouts), so they simply keep `entries`'
 * natural (`buildObjectTree` zLevel) relative order and sort after every
 * entry the saved order does cover.
 */
export function sortByPersistedOrder(
  entries: readonly ObjectTreeEntry[],
  order: readonly string[],
): readonly ObjectTreeEntry[] {
  const rank = new Map(order.map((id, index) => [id, index] as const));
  return entries
    .map((entry, naturalIndex) => ({ entry, naturalIndex, rank: rank.get(entry.id) }))
    .sort((a, b) => {
      if (a.rank !== undefined && b.rank !== undefined) return a.rank - b.rank;
      if (a.rank !== undefined) return -1;
      if (b.rank !== undefined) return 1;
      return a.naturalIndex - b.naturalIndex;
    })
    .map(({ entry }) => entry);
}
