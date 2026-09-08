// CH-6b — useChartLayout.ts의 복원/저장 조각: 서버 CH-8 레이아웃 상태를
// `layout/persistence.ts`로 fetch/save하고, 활성 패널 ↔ 화면(view) 간 최초
// 동기화(모델→뷰, 그 뒤 뷰→모델 거울)를 담당한다. 패널 CRUD는
// useChartLayoutPanels.ts로 옮겼다(P6 300줄 분할, 순수 이동).
import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import {
  createEmptyLayoutModel,
  type ChartLayoutModel,
  type ChartPanel,
} from "@aios/chart-engine/src/layout/layoutModel";
import {
  createLayout as createLayoutRecord,
  deleteLayout as deleteLayoutRecord,
  listLayouts,
  loadLayout,
  saveLayout,
  type ChartingPort,
  type SavedLayout,
} from "@aios/chart-engine/src/layout/persistence";
import { panelToView, panelViewFields, sameView, type ChartViewSnapshot } from "./chartLayoutView";

export type ChartLayoutStatus = "loading" | "ready" | "restore_failed";
export type ChartLayoutSaveStatus = "idle" | "saving" | "conflict" | "not_found" | "error";

const DEFAULT_LAYOUT_NAME = "기본 레이아웃";

export interface UseChartLayoutPersistenceOptions {
  readonly port: ChartingPort;
  readonly enabled: boolean;
  readonly view: ChartViewSnapshot;
  readonly onApplyViewRef: MutableRefObject<(view: ChartViewSnapshot) => void>;
  readonly genId: (prefix: string) => string;
}

export interface UseChartLayoutPersistenceResult {
  readonly status: ChartLayoutStatus;
  readonly restoreError: unknown;
  readonly retryRestore: () => void;
  readonly model: ChartLayoutModel;
  readonly setModel: Dispatch<SetStateAction<ChartLayoutModel>>;
  readonly layoutId: string | null;
  readonly layoutName: string;
  readonly isDirty: boolean;
  readonly setIsDirty: (dirty: boolean) => void;
  readonly saveStatus: ChartLayoutSaveStatus;
  readonly saveError: unknown;
  /** Resolves to the saved layout's id, or `null` if the save didn't succeed (conflict/not_found/error). */
  readonly save: () => Promise<string | null>;
  readonly reload: () => Promise<void>;
  readonly rename: (name: string) => void;
  readonly remove: () => Promise<void>;
}

export function useChartLayoutPersistence({
  port,
  enabled,
  view,
  onApplyViewRef,
  genId,
}: UseChartLayoutPersistenceOptions): UseChartLayoutPersistenceResult {
  const [status, setStatus] = useState<ChartLayoutStatus>("loading");
  const [restoreError, setRestoreError] = useState<unknown>(null);
  const [layoutId, setLayoutId] = useState<string | null>(null);
  const [layoutName, setLayoutName] = useState<string>(DEFAULT_LAYOUT_NAME);
  const [revision, setRevision] = useState<number | null>(null);
  const [model, setModel] = useState<ChartLayoutModel>(() => createEmptyLayoutModel());
  const [isDirty, setIsDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState<ChartLayoutSaveStatus>("idle");
  const [saveError, setSaveError] = useState<unknown>(null);
  const [syncToken, setSyncToken] = useState(0);

  const appliedTokenRef = useRef(0);
  const restoredOnceRef = useRef(false);

  const applyDefault = useCallback(() => {
    const id = genId("panel");
    const panel: ChartPanel = { id, ...panelViewFields(view), drawingSetId: id };
    setLayoutId(null);
    setRevision(null);
    setLayoutName(DEFAULT_LAYOUT_NAME);
    setModel({ ...createEmptyLayoutModel(), panels: [panel], activePanelId: id });
    setIsDirty(false);
  }, [view, genId]);

  const applySaved = useCallback((saved: SavedLayout) => {
    setLayoutId(saved.meta.id);
    setRevision(saved.meta.revision);
    setLayoutName(saved.meta.name);
    setModel(saved.model);
    setIsDirty(false);
  }, []);

  const restore = useCallback(async () => {
    setStatus("loading");
    setRestoreError(null);
    try {
      const saved = await listLayouts(port);
      const newest = [...saved].sort((a, b) => b.meta.updatedAt.localeCompare(a.meta.updatedAt))[0];
      if (newest) applySaved(newest);
      else applyDefault();
      setStatus("ready");
      setSyncToken((t) => t + 1);
    } catch (err) {
      setRestoreError(err);
      setStatus("restore_failed");
    }
  }, [port, applySaved, applyDefault]);

  useEffect(() => {
    if (!enabled || restoredOnceRef.current) return;
    restoredOnceRef.current = true;
    void restore();
  }, [enabled, restore]);

  // 복원/새로고침 직후 1회: 활성 패널을 화면(뷰)에 반영한다.
  useEffect(() => {
    if (status !== "ready" || appliedTokenRef.current === syncToken) return;
    appliedTokenRef.current = syncToken;
    const active = model.panels.find((p) => p.id === model.activePanelId) ?? model.panels[0];
    if (active) onApplyViewRef.current(panelToView(active));
  }, [status, syncToken, model, onApplyViewRef]);

  // 그 뒤로는 반대 방향: 화면(뷰)이 바뀌면(타임프레임·지표·비교 심볼 선택 등) 활성 패널에 거울처럼 반영한다.
  useEffect(() => {
    if (!enabled || status !== "ready" || appliedTokenRef.current !== syncToken) return;
    setModel((prev) => {
      const activeId = prev.activePanelId ?? prev.panels[0]?.id;
      const active = prev.panels.find((p) => p.id === activeId);
      if (!active || sameView(active, view)) return prev;
      setIsDirty(true);
      return { ...prev, panels: prev.panels.map((p) => (p.id === active.id ? { ...p, ...panelViewFields(view) } : p)) };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, status, syncToken, view.instrumentId, view.venue, view.timeframe, view.indicatorIds.join(","), view.compareSymbolIds.join(",")]);

  const save = useCallback(async () => {
    setSaveStatus("saving");
    setSaveError(null);
    try {
      let saved: SavedLayout;
      if (layoutId === null) {
        saved = await createLayoutRecord(port, layoutName, model);
      } else {
        const result = await saveLayout(port, layoutId, revision ?? 0, { name: layoutName, model });
        if (result.kind !== "ok") {
          setSaveStatus(result.kind);
          return null;
        }
        saved = result.value;
      }
      applySaved(saved);
      setSaveStatus("idle");
      return saved.meta.id;
    } catch (err) {
      setSaveError(err);
      setSaveStatus("error");
      return null;
    }
  }, [port, layoutId, layoutName, model, revision, applySaved]);

  const reload = useCallback(async () => {
    setSaveStatus("saving");
    setSaveError(null);
    try {
      if (layoutId === null) {
        await restore();
      } else {
        const result = await loadLayout(port, layoutId);
        if (result.kind === "ok") applySaved(result.value);
        else applyDefault();
        setStatus("ready");
        setSyncToken((t) => t + 1);
      }
      setSaveStatus("idle");
    } catch (err) {
      setSaveError(err);
      setSaveStatus("error");
    }
  }, [port, layoutId, restore, applySaved, applyDefault]);

  const rename = useCallback((name: string) => {
    setLayoutName(name);
    setIsDirty(true);
  }, []);

  const remove = useCallback(async () => {
    if (layoutId === null) {
      applyDefault();
      return;
    }
    setSaveStatus("saving");
    setSaveError(null);
    try {
      await deleteLayoutRecord(port, layoutId);
      applyDefault();
      setSaveStatus("idle");
    } catch (err) {
      setSaveError(err);
      setSaveStatus("error");
    }
  }, [port, layoutId, applyDefault]);

  return {
    status,
    restoreError,
    retryRestore: () => void restore(),
    model,
    setModel,
    layoutId,
    layoutName,
    isDirty,
    setIsDirty,
    saveStatus,
    saveError,
    save,
    reload,
    rename,
    remove,
  };
}
