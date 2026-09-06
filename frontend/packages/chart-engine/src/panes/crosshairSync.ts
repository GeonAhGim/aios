/**
 * CH-14 — crosshair time synchronization across panes.
 *
 * A multi-pane chart shares one time axis (CH-1b `core/timeScale.ts`) across
 * every pane, but each pane has its own price axis (`paneLayout.ts`). So the
 * only thing that needs synchronizing when the user moves the crosshair in
 * one pane is `timeMs`/`x` on that shared axis — every other pane resolves
 * its own `y`/price independently via its own `PriceScale`. This module is
 * the single source of truth for "what time is the crosshair at right now",
 * broadcast to every subscribing pane; it does not compute per-pane `y`.
 */

export interface CrosshairMove {
  readonly sourcePaneId: string;
  readonly x: number;
  readonly timeMs: number;
}

export type CrosshairSyncState =
  | { readonly kind: "hidden" }
  | { readonly kind: "visible"; readonly sourcePaneId: string; readonly x: number; readonly timeMs: number };

export interface CrosshairSync {
  /** Moves the crosshair; every subscriber (including panes other than `sourcePaneId`) observes the same `timeMs`. */
  move(update: CrosshairMove): void;
  /** Hides the crosshair in every pane (e.g. pointer left the chart area). */
  hide(): void;
  subscribe(listener: (state: CrosshairSyncState) => void): () => void;
  snapshot(): CrosshairSyncState;
  /** The `timeMs` every pane should render its crosshair at, or `null` when hidden. */
  currentTimeMs(): number | null;
}

function assertFinite(value: number, field: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`CrosshairMove.${field} must be finite, got ${value}`);
}

export function createCrosshairSync(): CrosshairSync {
  let state: CrosshairSyncState = { kind: "hidden" };
  const listeners = new Set<(state: CrosshairSyncState) => void>();

  function notify(): void {
    for (const listener of listeners) listener(state);
  }

  function move(update: CrosshairMove): void {
    if (update.sourcePaneId.length === 0) throw new RangeError("CrosshairMove.sourcePaneId must be a non-empty string");
    assertFinite(update.x, "x");
    assertFinite(update.timeMs, "timeMs");
    state = { kind: "visible", sourcePaneId: update.sourcePaneId, x: update.x, timeMs: update.timeMs };
    notify();
  }

  function hide(): void {
    if (state.kind === "hidden") return;
    state = { kind: "hidden" };
    notify();
  }

  function subscribe(listener: (state: CrosshairSyncState) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  return {
    move,
    hide,
    subscribe,
    snapshot: () => state,
    currentTimeMs: () => (state.kind === "visible" ? state.timeMs : null),
  };
}
