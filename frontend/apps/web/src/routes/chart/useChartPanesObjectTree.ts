// CH-16b — ChartPanes.tsx의 legend/object-tree 조립 조각: 표시 목록 소스,
// 순서/잠금 전이. legend/objectTree.ts가 순수 변환을 소유하고, 이 훅은
// 그 결과를 persisted order/locks 콜백으로 옮기는 배선만 맡는다.
import { useMemo, useState } from "react";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import {
  buildObjectTree,
  encodeObjectTreeState,
  moveEntry,
  setEntryLocked,
  sortByPersistedOrder,
  type ObjectTreeEntry,
  type ObjectTreeSource,
} from "@aios/chart-engine/src/legend/objectTree";
import { MAIN_PANE_ID, subPaneId } from "./chartPanesModel";

export interface UseChartPanesObjectTreeOptions {
  readonly mainOverlays: readonly OverlayEntry[];
  readonly subOverlays: readonly OverlayEntry[];
  readonly drawings: DrawingCollection;
  readonly objectTreeOrder: readonly string[];
  readonly lockedIndicatorIds: readonly string[];
  readonly onObjectTreeOrderChange?: (order: readonly string[]) => void;
  readonly onLockedIndicatorIdsChange?: (ids: readonly string[]) => void;
}

export interface UseChartPanesObjectTreeResult {
  readonly objectTree: readonly ObjectTreeEntry[];
  readonly hiddenIds: ReadonlySet<string>;
  readonly toggleHidden: (id: string) => void;
  readonly handleMoveEntry: (id: string, toIndex: number) => void;
  readonly handleToggleLocked: (id: string) => void;
}

export function useChartPanesObjectTree({
  mainOverlays,
  subOverlays,
  drawings,
  objectTreeOrder,
  lockedIndicatorIds,
  onObjectTreeOrderChange,
  onLockedIndicatorIdsChange,
}: UseChartPanesObjectTreeOptions): UseChartPanesObjectTreeResult {
  const [hiddenIds, setHiddenIds] = useState<ReadonlySet<string>>(new Set());

  const objectTreeSource: ObjectTreeSource = useMemo(
    () => ({
      getIndicators: () => [
        ...mainOverlays.map((o, i) => ({ id: o.id, paneId: MAIN_PANE_ID, name: o.id, visible: !hiddenIds.has(o.id), zLevel: i })),
        ...subOverlays.map((o, i) => ({
          id: o.id,
          paneId: subPaneId(o.id),
          name: o.id,
          visible: !hiddenIds.has(o.id),
          zLevel: mainOverlays.length + i,
        })),
      ],
      getOverlays: () =>
        drawings.map((d, i) => ({
          id: d.id,
          paneId: MAIN_PANE_ID,
          name: `${d.kind}:${d.id}`,
          visible: !hiddenIds.has(d.id),
          zLevel: 1000 + i,
          lock: false,
        })),
    }),
    [mainOverlays, subOverlays, drawings, hiddenIds],
  );
  const lockedIndicatorIdSet = useMemo(() => new Set(lockedIndicatorIds), [lockedIndicatorIds]);
  const objectTree = useMemo(
    () => sortByPersistedOrder(buildObjectTree(objectTreeSource, lockedIndicatorIdSet), objectTreeOrder),
    [objectTreeSource, lockedIndicatorIdSet, objectTreeOrder],
  );

  // CH-16b: legend/objectTree.ts owns the pure order/lock transitions — this
  // handler only translates a legend interaction into the next persisted
  // `objectTreeOrder`/`lockedIndicatorIds` and hands it to the caller
  // (ChartPage → useChartLayout.ts). Only indicators are lockable here:
  // overlays/drawings already carry their own native `locked` field (CH-4
  // drawings/model.ts) which `objectTreeSource.getOverlays()` above still
  // reports as a fixed `false` — CH-4's own lock UI is a separate leaf.
  const handleMoveEntry = (id: string, toIndex: number) => {
    onObjectTreeOrderChange?.(encodeObjectTreeState(moveEntry(objectTree, id, toIndex)).order);
  };
  const handleToggleLocked = (id: string) => {
    const entry = objectTree.find((e) => e.id === id);
    if (!entry || entry.kind !== "indicator") return;
    const next = setEntryLocked(objectTree, id, !entry.locked);
    onLockedIndicatorIdsChange?.(encodeObjectTreeState(next).locked);
  };
  const toggleHidden = (id: string) => {
    setHiddenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return { objectTree, hiddenIds, toggleHidden, handleMoveEntry, handleToggleLocked };
}
