/**
 * CH-14 — pane CRUD + height-ratio invariant.
 *
 * Pure model, no renderer/vendor dependency (composes below CH-8
 * `layout/layoutModel.ts`: a `ChartPanel` owns zero or one of these — wiring
 * that in is a later leaf's job, not reinvented here). Placement (main vs.
 * sub-pane) is CH-3 `indicators/overlayRegistry.ts`'s job; this module only
 * owns the CRUD/height side of the pane stack that placement renders into.
 *
 * Invariant held by every returned `PaneModel`: `heightRatio` sums to 1
 * across all panes (epsilon-tolerant for float drift), and exactly one pane
 * has `kind: "main"` — it is never removable, so there is always at least
 * one pane. `addPane`/`removePane` auto-redistribute proportionally so the
 * invariant holds by construction; `setHeightRatios` is the one entry point
 * that accepts caller-supplied ratios directly and fail-closed rejects a
 * sum that drifts from 1 instead of silently renormalizing it.
 */

export type PaneKind = "main" | "sub";

export interface PaneSpec {
  readonly id: string;
  readonly kind: PaneKind;
  /** Fraction of total chart height, in (0, 1). Sums to 1 across `PaneModel.panes`. */
  readonly heightRatio: number;
}

export interface PaneModel {
  /** Main pane first, then sub-panes in creation order. */
  readonly panes: readonly PaneSpec[];
}

export type PaneModelErrorCode =
  | "PANE_EMPTY_ID"
  | "PANE_DUPLICATE_ID"
  | "PANE_NOT_FOUND"
  | "PANE_HEIGHT_INVALID"
  | "PANE_HEIGHT_SUM_INVALID"
  | "PANE_LAST_MAIN_PANE";

export class PaneModelError extends Error {
  readonly code: PaneModelErrorCode;

  constructor(code: PaneModelErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "PaneModelError";
    this.code = code;
  }
}

const HEIGHT_EPSILON = 1e-6;

function assertNonEmptyId(id: string): void {
  if (id.length === 0) throw new PaneModelError("PANE_EMPTY_ID", "pane id must be a non-empty string");
}

function assertSumsToOne(panes: readonly PaneSpec[]): void {
  const sum = panes.reduce((total, p) => total + p.heightRatio, 0);
  if (Math.abs(sum - 1) > HEIGHT_EPSILON) {
    throw new PaneModelError("PANE_HEIGHT_SUM_INVALID", `height ratios sum to ${sum}, expected 1`);
  }
}

function findPane(model: PaneModel, id: string): PaneSpec {
  const pane = model.panes.find((p) => p.id === id);
  if (!pane) throw new PaneModelError("PANE_NOT_FOUND", `no pane with id "${id}"`);
  return pane;
}

/** A fresh model with just the (unremovable) main pane occupying the full height. */
export function createPaneModel(mainPaneId: string): PaneModel {
  assertNonEmptyId(mainPaneId);
  return { panes: [{ id: mainPaneId, kind: "main", heightRatio: 1 }] };
}

export interface AddPaneOptions {
  /** Fraction of total height the new pane takes; existing panes shrink proportionally to make room. Default splits evenly with the incoming pane count. */
  readonly heightRatio?: number;
}

/** Appends a sub-pane, shrinking every existing pane proportionally so the total stays 1. */
export function addPane(model: PaneModel, id: string, options: AddPaneOptions = {}): PaneModel {
  assertNonEmptyId(id);
  if (model.panes.some((p) => p.id === id)) {
    throw new PaneModelError("PANE_DUPLICATE_ID", `pane "${id}" already exists`);
  }
  const heightRatio = options.heightRatio ?? 1 / (model.panes.length + 1);
  if (!(heightRatio > 0 && heightRatio < 1)) {
    throw new PaneModelError("PANE_HEIGHT_INVALID", `heightRatio must be in (0, 1), got ${heightRatio}`);
  }
  const shrink = 1 - heightRatio;
  const panes: PaneSpec[] = [
    ...model.panes.map((p) => ({ ...p, heightRatio: p.heightRatio * shrink })),
    { id, kind: "sub" as const, heightRatio },
  ];
  assertSumsToOne(panes);
  return { panes };
}

/** Removes a sub-pane, redistributing its height proportionally across the remaining panes. Refuses to remove the main pane. */
export function removePane(model: PaneModel, id: string): PaneModel {
  const target = findPane(model, id);
  if (target.kind === "main") {
    throw new PaneModelError("PANE_LAST_MAIN_PANE", `cannot remove the main pane "${id}"`);
  }
  const remaining = model.panes.filter((p) => p.id !== id);
  const remainingTotal = remaining.reduce((total, p) => total + p.heightRatio, 0);
  const panes = remaining.map((p) => ({
    ...p,
    heightRatio: p.heightRatio + target.heightRatio * (p.heightRatio / remainingTotal),
  }));
  assertSumsToOne(panes);
  return { panes };
}

/** Sets one pane's height directly, scaling every other pane proportionally so the total stays 1. */
export function resizePane(model: PaneModel, id: string, heightRatio: number): PaneModel {
  findPane(model, id);
  if (!(heightRatio > 0 && heightRatio < 1)) {
    throw new PaneModelError("PANE_HEIGHT_INVALID", `heightRatio must be in (0, 1), got ${heightRatio}`);
  }
  const others = model.panes.filter((p) => p.id !== id);
  const othersTotal = others.reduce((total, p) => total + p.heightRatio, 0);
  const remainder = 1 - heightRatio;
  const scale = remainder / othersTotal;
  const panes = model.panes.map((p) => (p.id === id ? { ...p, heightRatio } : { ...p, heightRatio: p.heightRatio * scale }));
  assertSumsToOne(panes);
  return { panes };
}

/**
 * Bulk-sets every pane's height ratio verbatim (e.g. committing a drag-resize
 * of several panes at once, or restoring a persisted layout). Unlike
 * `resizePane`, this does not auto-normalize: the caller's ratios must
 * already cover exactly the model's current pane ids and sum to 1, or the
 * call is fail-closed rejected rather than silently renormalized.
 */
export function setHeightRatios(model: PaneModel, ratios: Readonly<Record<string, number>>): PaneModel {
  const providedIds = Object.keys(ratios);
  const modelIds = new Set(model.panes.map((p) => p.id));
  for (const id of providedIds) {
    if (!modelIds.has(id)) throw new PaneModelError("PANE_NOT_FOUND", `no pane with id "${id}"`);
  }
  if (providedIds.length !== model.panes.length) {
    throw new PaneModelError(
      "PANE_HEIGHT_INVALID",
      `ratios must cover every pane: got ${providedIds.length}, expected ${model.panes.length}`,
    );
  }
  const panes = model.panes.map((p) => {
    const heightRatio = ratios[p.id]!;
    if (!(heightRatio > 0 && heightRatio < 1)) {
      throw new PaneModelError("PANE_HEIGHT_INVALID", `heightRatio for "${p.id}" must be in (0, 1), got ${heightRatio}`);
    }
    return { ...p, heightRatio };
  });
  assertSumsToOne(panes);
  return { panes };
}

export function mainPane(model: PaneModel): PaneSpec {
  const pane = model.panes.find((p) => p.kind === "main");
  if (!pane) throw new PaneModelError("PANE_LAST_MAIN_PANE", "model has no main pane");
  return pane;
}
