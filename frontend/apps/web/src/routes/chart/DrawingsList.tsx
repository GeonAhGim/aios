// CH-6a 순수 이동(task-2011): ChartPage.tsx의 그리기 목록 섹션 JSX를 그대로 옮긴다 —
// 로직 변경 없음.
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import { drawingLabel } from "./chartPageHelpers";

export interface DrawingsListProps {
  readonly drawings: DrawingCollection;
  readonly onRemove: (id: string) => void;
}

export function DrawingsList({ drawings, onRemove }: DrawingsListProps) {
  return (
    <section aria-label="그리기 목록" className="space-y-1.5">
      <h2 className="text-sm font-medium text-fg-secondary">그리기 ({drawings.length})</h2>
      {drawings.length > 0 && (
        <ul className="space-y-1">
          {drawings.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-2 text-sm text-fg">
              <span>{drawingLabel(d)}</span>
              <button type="button" className="text-xs text-danger underline" onClick={() => onRemove(d.id)}>
                삭제
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
