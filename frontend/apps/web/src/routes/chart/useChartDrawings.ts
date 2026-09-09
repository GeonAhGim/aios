// CH-4b (task-2012): server-backed drawings persistence. Wires this hook to
// CH-8 `layout/persistence.ts` loadDrawings/saveDrawings (chart-engine, task-1614)
// exactly the way useChartLayout.ts wires the layout itself — same `ChartingPort`,
// same optimistic-locking contract (409 STATE_CONCURRENCY_CONFLICT never
// auto-overwrites; 404 RESOURCE_NOT_FOUND is surfaced, never folded into "no
// drawings"). No new client method/route: reuses chartingClient.getDrawings/
// putDrawings via the same port ChartPage already passes to useChartLayout.
//
// Drawings are keyed by `layoutId`, which only exists once the layout itself has
// been saved at least once (server always creates an empty drawing set alongside
// a new layout — see get_drawings.py docstring). So:
//  - restore: runs once, when the layout's initial restore settles ("ready"). If
//    that resolved to an *existing* saved layout (layoutId already set), its
//    drawings are loaded from the server. If it resolved to the unsaved default
//    layout (layoutId null), there is nothing to load yet — local (empty) state
//    stands, matching useChartLayout's applyDefault().
//  - persist: called by the caller (ChartPage, after `layout.save()` resolves a
//    layoutId) with that id. Never invented here — this hook has no fetch timer
//    of its own, so it can't race the "저장" button.
//
// loadDrawings/saveDrawings classify 404/409 via routeApiError internally
// (persistence.ts) and hand back a plain `{kind}` — this hook keys its own
// status off that same kind rather than re-deriving it, and reserves the raw
// caught error (routed again in ChartDrawingsErrorBanners.tsx, same convention
// as ChartLayoutErrorBanners.tsx) for genuinely unexpected failures (5xx etc.).
import { type Dispatch, type SetStateAction, useCallback, useEffect, useRef, useState } from "react";
import { loadDrawings, saveDrawings, type ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import { addDrawing, removeDrawing } from "@aios/chart-engine/src/drawings/tools";
import type { DrawingCollection, DrawingKind } from "@aios/chart-engine/src/drawings/model";
import { serializeDrawings } from "@aios/chart-engine/src/drawings/serialize";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { Timeframe, Venue } from "@aios/shared-types";
import type { ChartLayoutStatus } from "./useChartLayout";
import { createDrawing } from "./chartPageHelpers";

// CH-4c (task-2101): chartToolbarLayoutProps.ts chains persistDrawings(layoutId) onto
// every single "레이아웃 저장" click (CH-4b, a4269edc) — even when the user only
// changed a non-drawing setting (timeframe, indicators) and drew nothing. Without a
// same-content check, that means a drawings PUT (and a drawings revision bump) fires
// on every layout save, which both wastes a round trip and can spuriously invalidate
// another open tab's `expectedRevision` for content that never actually changed.
// `lastSyncedRef` holds the chart-engine `serializeDrawings` output for the drawing
// set last known to match the server (set on successful restore/persist); `persist()`
// compares against it and skips the network call when nothing to send actually
// changed. This is the actual (not test-only) production use of CH-4's serializer —
// see unwired-modules-baseline.json's now-removed "drawings/serialize" entry.

export type ChartDrawingsRestoreStatus = "idle" | "loading" | "ready" | "not_found" | "restore_failed";
export type ChartDrawingsSaveStatus = "idle" | "saving" | "conflict" | "not_found" | "error";

export interface UseChartDrawingsOptions {
  readonly port: ChartingPort;
  /** Mirrors useChartLayout's resolved id — null until the layout is saved once. */
  readonly layoutId: string | null;
  /** Gates the one-shot initial load on the layout's own restore settling first. */
  readonly layoutStatus: ChartLayoutStatus;
}

export interface UseChartDrawingsResult {
  readonly drawingTool: DrawingKind | null;
  readonly setDrawingTool: Dispatch<SetStateAction<DrawingKind | null>>;
  readonly drawings: DrawingCollection;
  readonly handleAddDrawing: (latestCandle: StreamCandle | undefined) => void;
  readonly handleRemoveDrawing: (id: string) => void;
  readonly restoreStatus: ChartDrawingsRestoreStatus;
  /** Raw error — only set for the unexpected/"restore_failed" case, not for the classified "not_found" one. */
  readonly restoreError: unknown;
  readonly retryRestore: () => void;
  readonly saveStatus: ChartDrawingsSaveStatus;
  /** Raw error — only set for the unexpected/"error" case, not for the classified "conflict"/"not_found" ones. */
  readonly saveError: unknown;
  /** Persists the current local drawings against `layoutId` (the just-resolved id from `layout.save()`). */
  readonly persist: (layoutId: string) => Promise<void>;
  /** Re-runs persist() against the hook's current `layoutId` option — mirrors retryRestore's pattern for the save side. */
  readonly retryPersist: () => void;
}

export function useChartDrawings(
  venue: Venue,
  instrumentId: string | null,
  timeframe: Timeframe,
  { port, layoutId, layoutStatus }: UseChartDrawingsOptions,
): UseChartDrawingsResult {
  const [drawingTool, setDrawingTool] = useState<DrawingKind | null>(null);
  const [drawings, setDrawings] = useState<DrawingCollection>([]);
  const drawingSeq = useRef(0);
  const revisionRef = useRef<number | null>(null);
  const restoredOnceRef = useRef(false);
  // CH-4c: last drawing set known to match the server, encoded via serializeDrawings.
  const lastSyncedRef = useRef<string | null>(null);

  const [restoreStatus, setRestoreStatus] = useState<ChartDrawingsRestoreStatus>("idle");
  const [restoreError, setRestoreError] = useState<unknown>(null);
  const [saveStatus, setSaveStatus] = useState<ChartDrawingsSaveStatus>("idle");
  const [saveError, setSaveError] = useState<unknown>(null);

  // 심볼/거래소/타임프레임이 바뀔 때마다 그리기를 초기화한다 — 서로 다른
  // 키의 그리기를 한 화면에 섞지 않는다(candleStream.ts key_mismatch 규약과 동일 축).
  useEffect(() => {
    if (!instrumentId) return;
    setDrawings([]);
  }, [venue, instrumentId, timeframe]);

  const restore = useCallback(
    async (id: string) => {
      setRestoreStatus("loading");
      setRestoreError(null);
      try {
        const result = await loadDrawings(port, id);
        if (result.kind === "not_found") {
          setRestoreStatus("not_found");
          return;
        }
        revisionRef.current = result.value.meta.revision;
        setDrawings(result.value.drawings);
        lastSyncedRef.current = serializeDrawings(result.value.drawings);
        setRestoreStatus("ready");
      } catch (err) {
        setRestoreError(err);
        setRestoreStatus("restore_failed");
      }
    },
    [port],
  );

  // 최초 1회: layout의 복원이 끝난 시점의 layoutId를 그대로 따른다. 그 시점에
  // layoutId가 이미 있으면(서버에 저장된 레이아웃을 복원한 경우) 그 도형 세트를
  // 불러오고, null이면(아직 한 번도 저장 안 한 기본 레이아웃) 불러올 것이 없다 —
  // 이후 layoutId가 최초 저장으로 생기더라도 여기서 다시 불러오지 않는다(로컬에서
  // 막 그린 도형을 서버의 빈 문서로 덮어쓰지 않기 위함, persist()가 그 몫을 맡는다).
  useEffect(() => {
    if (layoutStatus !== "ready" || restoredOnceRef.current) return;
    restoredOnceRef.current = true;
    if (layoutId !== null) void restore(layoutId);
    else setRestoreStatus("ready");
  }, [layoutStatus, layoutId, restore]);

  const retryRestore = useCallback(() => {
    if (layoutId !== null) void restore(layoutId);
  }, [layoutId, restore]);

  const persist = useCallback(
    async (id: string) => {
      // CH-4c: skip the PUT entirely when the encoded drawing set already matches
      // what the server has — see the file-header comment for why this matters.
      const encoded = serializeDrawings(drawings);
      if (encoded === lastSyncedRef.current) {
        setSaveStatus("idle");
        setSaveError(null);
        return;
      }
      setSaveStatus("saving");
      setSaveError(null);
      try {
        const result = await saveDrawings(port, id, revisionRef.current ?? 0, drawings);
        if (result.kind !== "ok") {
          // 409/404 — 로컬 도형을 지우거나 조용히 비우지 않는다(decision: task-2012 DoD).
          setSaveStatus(result.kind);
          return;
        }
        revisionRef.current = result.value.meta.revision;
        setDrawings(result.value.drawings);
        lastSyncedRef.current = serializeDrawings(result.value.drawings);
        setSaveStatus("idle");
      } catch (err) {
        setSaveError(err);
        setSaveStatus("error");
      }
    },
    [port, drawings],
  );

  const retryPersist = useCallback(() => {
    if (layoutId !== null) void persist(layoutId);
  }, [layoutId, persist]);

  function handleAddDrawing(latestCandle: StreamCandle | undefined): void {
    if (!drawingTool) return;
    if (!latestCandle) return;
    const time = Math.floor(latestCandle.openTimeMs / 1000);
    const price = Number(latestCandle.record.close);
    drawingSeq.current += 1;
    const drawing = createDrawing(`drawing-${drawingSeq.current}`, drawingTool, time, price);
    setDrawings((prev) => addDrawing(prev, drawing));
  }

  function handleRemoveDrawing(id: string): void {
    setDrawings((prev) => removeDrawing(prev, id));
  }

  return {
    drawingTool,
    setDrawingTool,
    drawings,
    handleAddDrawing,
    handleRemoveDrawing,
    restoreStatus,
    restoreError,
    retryRestore,
    saveStatus,
    saveError,
    persist,
    retryPersist,
  };
}
