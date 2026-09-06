/**
 * CH-8 — server-backed persistence for `ChartLayoutModel` + CH-4 drawing sets.
 *
 * Talks only to an injected `ChartingPort` (dependency injection) — this
 * module has no fetch/vendor dependency, so it stays a pure, unit-testable
 * orchestration layer. The concrete port implementation
 * (`@aios/api-client` `clients/charting.ts`) is wired by the composing app.
 *
 * Server contract: `src/api/routers/charting.py` (CH-5, task-1557 06e5560) —
 * `layout_state` is opaque (encoded/decoded here via `layoutModel.ts`), and
 * each layout has exactly one drawing set (`ports/repository.py` docstring)
 * encoded/decoded via CH-4 `../drawings/serialize.ts` (no new wire format).
 *
 * Optimistic locking (105번 표준): the port throws on non-2xx, exactly like
 * a real HTTP client. `routeApiError` (`@aios/shared-types`, the single
 * entry point named in this leaf's decision) classifies that thrown value —
 * `"refetch_retry"` (409 `STATE_CONCURRENCY_CONFLICT`) surfaces as
 * `{ kind: "conflict" }` and is never auto-resolved by overwriting, and
 * `"not_found"` (404, including the cross-tenant case — the server folds
 * both into the same code so this layer can't and shouldn't try to tell
 * them apart) surfaces as `{ kind: "not_found" }`. Any other classification
 * is a genuine unexpected failure and is rethrown as-is.
 */

import { routeApiError } from "@aios/shared-types";
import type { DrawingCollection } from "../drawings/model";
import { fromDrawingsDocument, toDrawingsDocument } from "../drawings/serialize";
import { type ChartLayoutModel, decodeLayoutModel, encodeLayoutModel } from "./layoutModel";

export interface LayoutMeta {
  readonly id: string;
  readonly name: string;
  readonly revision: number;
  readonly updatedAt: string;
}

export interface DrawingsMeta {
  readonly layoutId: string;
  readonly revision: number;
  readonly updatedAt: string;
}

export interface ChartingLayoutRecord {
  readonly id: string;
  readonly name: string;
  readonly layoutState: Record<string, unknown>;
  readonly revision: number;
  readonly updatedAt: string;
}

export interface ChartingDrawingsRecord {
  readonly layoutId: string;
  /** Raw, still-encoded document (CH-4 `DrawingsDocument` shape) — decoded here via `fromDrawingsDocument`. */
  readonly document: unknown;
  readonly revision: number;
  readonly updatedAt: string;
}

export interface CreateLayoutInput {
  readonly name: string;
  readonly layoutState: Record<string, unknown>;
}

export interface UpdateLayoutInput {
  readonly expectedRevision: number;
  readonly name?: string;
  readonly layoutState?: Record<string, unknown>;
}

export interface PutDrawingsInput {
  readonly expectedRevision: number;
  readonly schemaVersion: number;
  readonly drawings: readonly unknown[];
}

/** Injected port — matches `@aios/api-client` `clients/charting.ts` 1:1 (server SSOT: `charting.py`). */
export interface ChartingPort {
  createLayout(input: CreateLayoutInput): Promise<ChartingLayoutRecord>;
  listLayouts(): Promise<readonly ChartingLayoutRecord[]>;
  getLayout(layoutId: string): Promise<ChartingLayoutRecord>;
  updateLayout(layoutId: string, input: UpdateLayoutInput): Promise<ChartingLayoutRecord>;
  deleteLayout(layoutId: string): Promise<void>;
  getDrawings(layoutId: string): Promise<ChartingDrawingsRecord>;
  putDrawings(layoutId: string, input: PutDrawingsInput): Promise<ChartingDrawingsRecord>;
}

export type LoadResult<T> =
  | { readonly kind: "ok"; readonly value: T }
  | { readonly kind: "not_found" };

export type SaveResult<T> =
  | { readonly kind: "ok"; readonly value: T }
  | { readonly kind: "conflict" }
  | { readonly kind: "not_found" };

export type DeleteResult = { readonly kind: "ok" } | { readonly kind: "not_found" };

// Never auto-resolves: unrecognized failures (network errors, 5xx, unrelated
// 4xx) propagate as-is so callers don't mistake them for "no data".
async function classify<T>(run: () => Promise<T>): Promise<
  { readonly kind: "ok"; readonly value: T } | { readonly kind: "conflict" } | { readonly kind: "not_found" }
> {
  try {
    return { kind: "ok", value: await run() };
  } catch (err) {
    const routed = routeApiError(err);
    if (routed.kind === "not_found") return { kind: "not_found" };
    if (routed.kind === "refetch_retry") return { kind: "conflict" };
    throw err;
  }
}

function toLayoutMeta(record: ChartingLayoutRecord): LayoutMeta {
  return { id: record.id, name: record.name, revision: record.revision, updatedAt: record.updatedAt };
}

function toDrawingsMeta(record: ChartingDrawingsRecord): DrawingsMeta {
  return { layoutId: record.layoutId, revision: record.revision, updatedAt: record.updatedAt };
}

export interface SavedLayout {
  readonly meta: LayoutMeta;
  readonly model: ChartLayoutModel;
}

export async function createLayout(
  port: ChartingPort,
  name: string,
  model: ChartLayoutModel,
): Promise<SavedLayout> {
  const record = await port.createLayout({ name, layoutState: encodeLayoutModel(model) });
  return { meta: toLayoutMeta(record), model: decodeLayoutModel(record.layoutState) };
}

// GET에는 낙관적 잠금이 없어 classify()가 "conflict"를 반환할 일이 없다 —
// deleteLayout과 동일하게 LoadResult(ok|not_found)로 명시적으로 좁힌다.
export async function loadLayout(port: ChartingPort, layoutId: string): Promise<LoadResult<SavedLayout>> {
  const outcome = await classify(() => port.getLayout(layoutId));
  if (outcome.kind === "not_found") return outcome;
  if (outcome.kind === "conflict") throw new Error("unexpected conflict classification on getLayout");
  const record = outcome.value;
  return { kind: "ok", value: { meta: toLayoutMeta(record), model: decodeLayoutModel(record.layoutState) } };
}

/** Restores every saved multi-chart layout + watchlist set for the current tenant. */
export async function listLayouts(port: ChartingPort): Promise<readonly SavedLayout[]> {
  const records = await port.listLayouts();
  return records.map((record) => ({ meta: toLayoutMeta(record), model: decodeLayoutModel(record.layoutState) }));
}

export async function saveLayout(
  port: ChartingPort,
  layoutId: string,
  expectedRevision: number,
  updates: { readonly name?: string; readonly model?: ChartLayoutModel },
): Promise<SaveResult<SavedLayout>> {
  const outcome = await classify(() =>
    port.updateLayout(layoutId, {
      expectedRevision,
      name: updates.name,
      layoutState: updates.model !== undefined ? encodeLayoutModel(updates.model) : undefined,
    }),
  );
  if (outcome.kind !== "ok") return outcome;
  const record = outcome.value;
  return { kind: "ok", value: { meta: toLayoutMeta(record), model: decodeLayoutModel(record.layoutState) } };
}

// delete_layout takes no expected_revision (ports/repository.py) — a "conflict"
// classification can't actually happen here, but classify()'s return type is
// shared, so it's narrowed explicitly rather than widening DeleteResult.
export async function deleteLayout(port: ChartingPort, layoutId: string): Promise<DeleteResult> {
  const outcome = await classify(() => port.deleteLayout(layoutId));
  if (outcome.kind === "not_found") return outcome;
  if (outcome.kind === "conflict") throw new Error("unexpected conflict classification on delete_layout");
  return { kind: "ok" };
}

export interface SavedDrawings {
  readonly meta: DrawingsMeta;
  readonly drawings: DrawingCollection;
}

export async function loadDrawings(port: ChartingPort, layoutId: string): Promise<LoadResult<SavedDrawings>> {
  const outcome = await classify(() => port.getDrawings(layoutId));
  if (outcome.kind === "not_found") return outcome;
  if (outcome.kind === "conflict") throw new Error("unexpected conflict classification on getDrawings");
  const record = outcome.value;
  return { kind: "ok", value: { meta: toDrawingsMeta(record), drawings: fromDrawingsDocument(record.document) } };
}

export async function saveDrawings(
  port: ChartingPort,
  layoutId: string,
  expectedRevision: number,
  drawings: DrawingCollection,
): Promise<SaveResult<SavedDrawings>> {
  const doc = toDrawingsDocument(drawings);
  const outcome = await classify(() =>
    port.putDrawings(layoutId, { expectedRevision, schemaVersion: doc.schema_version, drawings: doc.drawings }),
  );
  if (outcome.kind !== "ok") return outcome;
  const record = outcome.value;
  return { kind: "ok", value: { meta: toDrawingsMeta(record), drawings: fromDrawingsDocument(record.document) } };
}
