// CH-6b — 서버 저장·복원 배선. CH-8(task-1593) layout/persistence.ts + layoutModel.ts만
// 소비한다(화면에서 fetch 직접 호출 금지, decision). ChartPage.tsx는 이 훅을 통해서만
// 레이아웃 상태를 다루고, 렌더링에 필요한 값과 사용자 액션만 돌려받는다 — ChartPage를
// ≤300줄로 유지하기 위한 훅 추출이 이 파일의 존재 이유다.
//
// "뷰"(현재 화면에 보이는 심볼·타임프레임·지표)는 ChartPage가 소유한 로컬 state
// (venue/timeframe/instrumentId/selectedIndicatorIds, 전부 6a 원본)를 그대로 두고,
// 이 훅은 `view`로 매 렌더 전달받아 활성 패널에 거울처럼 반영한다(모델→뷰 방향은
// onApplyView 콜백으로 역전파). 두 방향 동기화가 서로를 되먹임하지 않도록
// appliedTokenRef 하나로 순서를 고정한다: 복원/새로고침 직후엔 먼저 모델→뷰를
// 적용하고, 그 적용이 끝난 뒤에야 뷰→모델 거울 effect가 움직인다.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createEmptyLayoutModel,
  type ChartLayoutModel,
  type ChartPanel,
  type IndicatorRef,
  type InstrumentRef,
  type Watchlist,
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
export type ChartLayoutStatus = "loading" | "ready" | "restore_failed";
export type ChartLayoutSaveStatus = "idle" | "saving" | "conflict" | "not_found" | "error";

export interface ChartViewSnapshot {
  readonly instrumentId: string;
  readonly venue: string;
  readonly timeframe: string;
  readonly indicatorIds: readonly string[];
}

export interface UseChartLayoutOptions {
  readonly port: ChartingPort;
  readonly enabled: boolean;
  readonly view: ChartViewSnapshot;
  /** 복원/새로고침/패널 전환으로 활성 패널이 바뀔 때 화면에 반영하도록 호출된다. */
  readonly onApplyView: (view: ChartViewSnapshot) => void;
}

export interface UseChartLayoutResult {
  readonly status: ChartLayoutStatus;
  readonly restoreError: unknown;
  readonly retryRestore: () => void;
  readonly model: ChartLayoutModel;
  readonly layoutName: string;
  readonly isDirty: boolean;
  readonly saveStatus: ChartLayoutSaveStatus;
  readonly saveError: unknown;
  readonly save: () => Promise<void>;
  readonly reload: () => Promise<void>;
  readonly rename: (name: string) => void;
  readonly remove: () => Promise<void>;
  readonly addPanel: () => void;
  readonly removePanel: (panelId: string) => void;
  readonly setActivePanel: (panelId: string) => void;
  readonly toggleWatchlistEntry: (entry: InstrumentRef) => void;
}

const DEFAULT_LAYOUT_NAME = "기본 레이아웃";
const DEFAULT_WATCHLIST_ID = "default";
const DEFAULT_WATCHLIST_NAME = "기본";

function panelViewFields(view: ChartViewSnapshot): Pick<ChartPanel, "instrument" | "timeframe" | "indicators"> {
  return {
    instrument: { instrumentId: view.instrumentId, venue: view.venue, symbol: view.instrumentId },
    timeframe: view.timeframe,
    indicators: view.indicatorIds.map((id): IndicatorRef => ({ id })),
  };
}

function panelToView(panel: ChartPanel): ChartViewSnapshot {
  return {
    instrumentId: panel.instrument.instrumentId,
    venue: panel.instrument.venue,
    timeframe: panel.timeframe,
    indicatorIds: panel.indicators.map((i) => i.id),
  };
}

function sameView(panel: ChartPanel, view: ChartViewSnapshot): boolean {
  const v = panelToView(panel);
  return (
    v.instrumentId === view.instrumentId &&
    v.venue === view.venue &&
    v.timeframe === view.timeframe &&
    v.indicatorIds.length === view.indicatorIds.length &&
    v.indicatorIds.every((id, i) => id === view.indicatorIds[i])
  );
}

function sameInstrument(a: InstrumentRef, b: InstrumentRef): boolean {
  return a.instrumentId === b.instrumentId && a.venue === b.venue;
}

export function useChartLayout({ port, enabled, view, onApplyView }: UseChartLayoutOptions): UseChartLayoutResult {
  const idSeq = useRef(0);
  const genId = useCallback((prefix: string) => {
    idSeq.current += 1;
    return `${prefix}-${idSeq.current}`;
  }, []);

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

  const onApplyViewRef = useRef(onApplyView);
  onApplyViewRef.current = onApplyView;
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
  }, [status, syncToken, model]);

  // 그 뒤로는 반대 방향: 화면(뷰)이 바뀌면(타임프레임·지표 선택 등) 활성 패널에 거울처럼 반영한다.
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
  }, [enabled, status, syncToken, view.instrumentId, view.venue, view.timeframe, view.indicatorIds.join(",")]);

  const save = useCallback(async () => {
    setSaveStatus("saving");
    setSaveError(null);
    try {
      if (layoutId === null) {
        applySaved(await createLayoutRecord(port, layoutName, model));
      } else {
        const result = await saveLayout(port, layoutId, revision ?? 0, { name: layoutName, model });
        if (result.kind === "ok") applySaved(result.value);
        else {
          setSaveStatus(result.kind);
          return;
        }
      }
      setSaveStatus("idle");
    } catch (err) {
      setSaveError(err);
      setSaveStatus("error");
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

  const addPanel = useCallback(() => {
    const id = genId("panel");
    const panel: ChartPanel = { id, ...panelViewFields(view), drawingSetId: id };
    setModel((prev) => ({ ...prev, panels: [...prev.panels, panel], activePanelId: id }));
    setIsDirty(true);
  }, [view, genId]);

  const removePanel = useCallback((panelId: string) => {
    setModel((prev) => {
      const panels = prev.panels.filter((p) => p.id !== panelId);
      const wasActive = prev.activePanelId === panelId;
      const activePanelId = wasActive ? (panels[0]?.id ?? null) : prev.activePanelId;
      if (wasActive) {
        const nextActive = panels.find((p) => p.id === activePanelId);
        if (nextActive) onApplyViewRef.current(panelToView(nextActive));
      }
      return { ...prev, panels, activePanelId };
    });
    setIsDirty(true);
  }, []);

  const setActivePanel = useCallback((panelId: string) => {
    setModel((prev) => {
      if (prev.activePanelId === panelId || !prev.panels.some((p) => p.id === panelId)) return prev;
      const panel = prev.panels.find((p) => p.id === panelId);
      if (panel) onApplyViewRef.current(panelToView(panel));
      return { ...prev, activePanelId: panelId };
    });
    setIsDirty(true);
  }, []);

  const toggleWatchlistEntry = useCallback((entry: InstrumentRef) => {
    setModel((prev) => {
      const existing = prev.watchlists.find((w) => w.id === DEFAULT_WATCHLIST_ID);
      if (!existing) {
        const watchlist: Watchlist = { id: DEFAULT_WATCHLIST_ID, name: DEFAULT_WATCHLIST_NAME, entries: [entry] };
        return { ...prev, watchlists: [...prev.watchlists, watchlist] };
      }
      const has = existing.entries.some((e) => sameInstrument(e, entry));
      const entries = has ? existing.entries.filter((e) => !sameInstrument(e, entry)) : [...existing.entries, entry];
      const next: Watchlist = { ...existing, entries };
      return { ...prev, watchlists: prev.watchlists.map((w) => (w.id === DEFAULT_WATCHLIST_ID ? next : w)) };
    });
    setIsDirty(true);
  }, []);

  return {
    status,
    restoreError,
    retryRestore: () => void restore(),
    model,
    layoutName,
    isDirty,
    saveStatus,
    saveError,
    save,
    reload,
    rename,
    remove,
    addPanel,
    removePanel,
    setActivePanel,
    toggleWatchlistEntry,
  };
}
