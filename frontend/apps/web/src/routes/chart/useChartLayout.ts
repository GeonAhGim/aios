// CH-6b — 서버 저장·복원 배선. CH-8(task-1593) layout/persistence.ts + layoutModel.ts만
// 소비한다(화면에서 fetch 직접 호출 금지, decision). "뷰"(심볼·타임프레임·지표·CH-13b
// 비교 심볼)는 ChartPage가 소유한 로컬 state를 그대로 두고, 이 훅은 `view`로 매 렌더
// 전달받아 활성 패널에 거울처럼 반영한다(모델→뷰는 onApplyView로 역전파).
// appliedTokenRef로 순서를 고정한다: 복원/새로고침 직후엔 모델→뷰가 먼저, 그 뒤에야
// 뷰→모델 거울 effect가 움직인다.
//
// 복원/저장(useChartLayoutPersistence.ts)과 패널 CRUD(useChartLayoutPanels.ts)로
// 쪼갰다(P6 300줄 분할, 순수 이동) — 이 파일은 둘을 조립해 하나의 훅 계약으로
// 내보내는 배선만 남는다.
import { useCallback, useRef } from "react";
import type { ChartLayoutModel, InstrumentRef } from "@aios/chart-engine/src/layout/layoutModel";
import type { ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import { useChartLayoutPanels } from "./useChartLayoutPanels";
import {
  useChartLayoutPersistence,
  type ChartLayoutSaveStatus,
  type ChartLayoutStatus,
} from "./useChartLayoutPersistence";
import type { ChartViewSnapshot } from "./chartLayoutView";

export type { ChartLayoutSaveStatus, ChartLayoutStatus } from "./useChartLayoutPersistence";
export type { ChartViewSnapshot } from "./chartLayoutView";

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
  /** Server-assigned id once saved at least once — `null` for the unsaved default layout. CH-4b (task-2012) keys drawings persistence off this. */
  readonly layoutId: string | null;
  readonly layoutName: string;
  readonly isDirty: boolean;
  readonly saveStatus: ChartLayoutSaveStatus;
  readonly saveError: unknown;
  /** Resolves to the saved layout's id, or `null` if the save didn't succeed (conflict/not_found/error). */
  readonly save: () => Promise<string | null>;
  readonly reload: () => Promise<void>;
  readonly rename: (name: string) => void;
  readonly remove: () => Promise<void>;
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

export function useChartLayout({ port, enabled, view, onApplyView }: UseChartLayoutOptions): UseChartLayoutResult {
  const idSeq = useRef(0);
  const genId = useCallback((prefix: string) => {
    idSeq.current += 1;
    return `${prefix}-${idSeq.current}`;
  }, []);

  const onApplyViewRef = useRef(onApplyView);
  onApplyViewRef.current = onApplyView;

  const persistence = useChartLayoutPersistence({ port, enabled, view, onApplyViewRef, genId });
  const panels = useChartLayoutPanels({
    view,
    model: persistence.model,
    setModel: persistence.setModel,
    setIsDirty: persistence.setIsDirty,
    onApplyViewRef,
    genId,
  });

  return {
    status: persistence.status,
    restoreError: persistence.restoreError,
    retryRestore: persistence.retryRestore,
    model: persistence.model,
    layoutId: persistence.layoutId,
    layoutName: persistence.layoutName,
    isDirty: persistence.isDirty,
    saveStatus: persistence.saveStatus,
    saveError: persistence.saveError,
    save: persistence.save,
    reload: persistence.reload,
    rename: persistence.rename,
    remove: persistence.remove,
    addPanel: panels.addPanel,
    removePanel: panels.removePanel,
    setActivePanel: panels.setActivePanel,
    toggleWatchlistEntry: panels.toggleWatchlistEntry,
    objectTreeOrder: panels.objectTreeOrder,
    lockedIndicatorIds: panels.lockedIndicatorIds,
    setObjectTreeOrder: panels.setObjectTreeOrder,
    setLockedIndicatorIds: panels.setLockedIndicatorIds,
  };
}
