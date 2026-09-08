import { Alert, Button, Field, Input } from "@aios/ui-web";
import type { ChartLayoutControls } from "./ChartToolbar";
import { type ButtonSpec, useRovingToolbar } from "./useRovingToolbar";

interface LayoutPanelControlsProps {
  layout: ChartLayoutControls;
}

export function LayoutPanelControls({ layout }: LayoutPanelControlsProps) {
  const panelButtons: ButtonSpec[] = layout.panels.map((p) => ({ id: `panel-${p.id}`, disabled: false }));
  const { groupRef, tabIndexFor, onFocusButton, onKeyDown } = useRovingToolbar(panelButtons);
  const conflictMessage =
    layout.saveStatus === "conflict"
      ? "다른 세션이 먼저 저장했습니다. 자동으로 덮어쓰지 않습니다 — 최신 내용을 다시 불러온 뒤 다시 시도하세요."
      : layout.saveStatus === "not_found"
        ? "이 레이아웃은 다른 곳에서 삭제되었습니다."
        : null;

  return (
    <>
      <Field label="레이아웃 이름">
        <Input aria-label="레이아웃 이름" value={layout.name} onChange={(e) => layout.onNameChange(e.target.value)} />
      </Field>
      <Button type="button" variant="secondary" size="sm" disabled={layout.saveStatus === "saving"} onClick={layout.onSave}>
        레이아웃 저장
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={layout.onDelete}>
        레이아웃 삭제
      </Button>
      <Button type="button" variant="ghost" size="sm" aria-pressed={layout.isWatchlisted} onClick={layout.onToggleWatchlist}>
        {layout.isWatchlisted ? "관심목록 제거" : "관심목록 추가"}
      </Button>

      <div ref={groupRef} role="tablist" aria-label="차트 패널" className="flex items-center gap-1" onKeyDown={onKeyDown}>
        {layout.panels.map((p) => {
          const id = `panel-${p.id}`;
          return (
            <button
              key={p.id}
              id={id}
              type="button"
              role="tab"
              aria-selected={p.id === layout.activePanelId}
              tabIndex={tabIndexFor(id)}
              className={
                p.id === layout.activePanelId
                  ? "rounded bg-surface-hover px-2 py-1 text-xs font-medium text-fg"
                  : "rounded px-2 py-1 text-xs text-fg-secondary"
              }
              onFocus={() => onFocusButton(id)}
              onClick={() => layout.onSelectPanel(p.id)}
            >
              {p.label}
            </button>
          );
        })}
        <Button type="button" variant="ghost" size="sm" aria-label="패널 추가" onClick={layout.onAddPanel}>
          ＋
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="패널 제거"
          disabled={layout.panels.length <= 1}
          onClick={layout.onRemovePanel}
        >
          －
        </Button>
      </div>

      {conflictMessage && (
        <Alert tone="warning">
          <p>{conflictMessage}</p>
          <Button type="button" variant="secondary" size="sm" onClick={layout.onReload}>
            다시 불러오기
          </Button>
        </Alert>
      )}
    </>
  );
}
