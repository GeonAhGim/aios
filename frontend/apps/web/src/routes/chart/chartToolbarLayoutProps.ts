// CH-6a 순수 이동(task-2011): ChartPage.tsx가 ChartToolbar에 넘기던 layout prop
// 조립 로직을 그대로 옮긴다 — 로직 변경 없음.
import type { Venue } from "@aios/shared-types";
import type { ChartLayoutControls } from "./ChartToolbar";
import type { UseChartLayoutResult } from "./useChartLayout";

// CH-4b (task-2012): drawings persistence rides the same "레이아웃 저장" button —
// a layoutId (server-assigned) must exist before its drawing set can be PUT, and
// "저장" is the one place both are known to have just settled together.
export function buildChartLayoutControls(
  layout: UseChartLayoutResult,
  instrumentId: string,
  venue: Venue,
  persistDrawings: (layoutId: string) => Promise<void>,
): ChartLayoutControls {
  return {
    name: layout.layoutName,
    onNameChange: layout.rename,
    onSave: () => {
      void (async () => {
        const layoutId = await layout.save();
        if (layoutId !== null) await persistDrawings(layoutId);
      })();
    },
    onDelete: () => void layout.remove(),
    saveStatus: layout.saveStatus,
    onReload: () => void layout.reload(),
    panels: layout.model.panels.map((p) => ({ id: p.id, label: `${p.instrument.instrumentId} · ${p.timeframe}` })),
    activePanelId: layout.model.activePanelId,
    onSelectPanel: layout.setActivePanel,
    onAddPanel: layout.addPanel,
    onRemovePanel: () => {
      if (layout.model.activePanelId) layout.removePanel(layout.model.activePanelId);
    },
    isWatchlisted: layout.model.watchlists.some((w) =>
      w.entries.some((e) => e.instrumentId === instrumentId && e.venue === venue),
    ),
    onToggleWatchlist: () => layout.toggleWatchlistEntry({ instrumentId, venue, symbol: instrumentId }),
  };
}
