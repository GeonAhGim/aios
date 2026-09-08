// CH-6b/CH-16b — useChartLayout.ts의 패널 CRUD 조각: 패널 추가/제거/활성화,
// 워치리스트 토글, legend/object-tree order·lock 배선. 복원/저장은
// useChartLayoutPersistence.ts 소관(P6 300줄 분할, 순수 이동).
import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type {
  ChartLayoutModel,
  ChartPanel,
  InstrumentRef,
  Watchlist,
} from "@aios/chart-engine/src/layout/layoutModel";
import { panelToView, panelViewFields, sameInstrument, type ChartViewSnapshot } from "./chartLayoutView";

const DEFAULT_WATCHLIST_ID = "default";
const DEFAULT_WATCHLIST_NAME = "기본";
const EMPTY_STRING_ARRAY: readonly string[] = [];

export interface UseChartLayoutPanelsOptions {
  readonly view: ChartViewSnapshot;
  readonly model: ChartLayoutModel;
  readonly setModel: Dispatch<SetStateAction<ChartLayoutModel>>;
  readonly setIsDirty: (dirty: boolean) => void;
  readonly onApplyViewRef: MutableRefObject<(view: ChartViewSnapshot) => void>;
  readonly genId: (prefix: string) => string;
}

export interface UseChartLayoutPanelsResult {
  readonly addPanel: () => void;
  readonly removePanel: (panelId: string) => void;
  readonly setActivePanel: (panelId: string) => void;
  readonly toggleWatchlistEntry: (entry: InstrumentRef) => void;
  /** CH-16b: active panel's persisted legend/object-tree order (`legend/objectTree.ts` ids) — `[]` until reordered once. */
  readonly objectTreeOrder: readonly string[];
  /** CH-16b: active panel's persisted locked indicator ids — `[]` until locked once. */
  readonly lockedIndicatorIds: readonly string[];
  readonly setObjectTreeOrder: (order: readonly string[]) => void;
  readonly setLockedIndicatorIds: (ids: readonly string[]) => void;
}

export function useChartLayoutPanels({
  view,
  model,
  setModel,
  setIsDirty,
  onApplyViewRef,
  genId,
}: UseChartLayoutPanelsOptions): UseChartLayoutPanelsResult {
  const addPanel = useCallback(() => {
    const id = genId("panel");
    const panel: ChartPanel = { id, ...panelViewFields(view), drawingSetId: id };
    setModel((prev) => ({ ...prev, panels: [...prev.panels, panel], activePanelId: id }));
    setIsDirty(true);
  }, [view, genId, setModel, setIsDirty]);

  const removePanel = useCallback(
    (panelId: string) => {
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
    },
    [setModel, setIsDirty, onApplyViewRef],
  );

  const setActivePanel = useCallback(
    (panelId: string) => {
      setModel((prev) => {
        if (prev.activePanelId === panelId || !prev.panels.some((p) => p.id === panelId)) return prev;
        const panel = prev.panels.find((p) => p.id === panelId);
        if (panel) onApplyViewRef.current(panelToView(panel));
        return { ...prev, activePanelId: panelId };
      });
      setIsDirty(true);
    },
    [setModel, setIsDirty, onApplyViewRef],
  );

  const toggleWatchlistEntry = useCallback(
    (entry: InstrumentRef) => {
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
    },
    [setModel, setIsDirty],
  );

  // CH-16b: legend/objectTree.ts order/lock are panel-owned, mutated the same
  // way toggleWatchlistEntry mutates watchlists — never fetched/derived here,
  // ChartPanes.tsx (via legend/objectTree.ts) owns computing the next value.
  const activePanel = model.panels.find((p) => p.id === model.activePanelId);
  const objectTreeOrder = activePanel?.objectTreeOrder ?? EMPTY_STRING_ARRAY;
  const lockedIndicatorIds = activePanel?.lockedIndicatorIds ?? EMPTY_STRING_ARRAY;

  const setObjectTreeOrder = useCallback(
    (order: readonly string[]) => {
      setModel((prev) => {
        const activeId = prev.activePanelId;
        if (activeId === null) return prev;
        return { ...prev, panels: prev.panels.map((p) => (p.id === activeId ? { ...p, objectTreeOrder: order } : p)) };
      });
      setIsDirty(true);
    },
    [setModel, setIsDirty],
  );

  const setLockedIndicatorIds = useCallback(
    (ids: readonly string[]) => {
      setModel((prev) => {
        const activeId = prev.activePanelId;
        if (activeId === null) return prev;
        return { ...prev, panels: prev.panels.map((p) => (p.id === activeId ? { ...p, lockedIndicatorIds: ids } : p)) };
      });
      setIsDirty(true);
    },
    [setModel, setIsDirty],
  );

  return {
    addPanel,
    removePanel,
    setActivePanel,
    toggleWatchlistEntry,
    objectTreeOrder,
    lockedIndicatorIds,
    setObjectTreeOrder,
    setLockedIndicatorIds,
  };
}
