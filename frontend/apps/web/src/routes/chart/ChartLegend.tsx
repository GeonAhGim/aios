// CH-16 화면 배선: legend/objectTree.ts(buildObjectTree/setEntryVisible/
// moveEntry/setEntryLocked)를 그대로 조립만 한다 — 순서·표시·잠금 로직은 전부
// chart-engine 소관이고(ChartPanes.tsx가 moveEntry/setEntryLocked/
// encodeObjectTreeState를 실제로 호출한다) 여기서는 ObjectTreeEntry를 렌더링하고
// 사용자 조작을 그대로 콜백으로 위임만 한다(decision).
//
// CH-16b: 잠금 토글은 지표(kind === "indicator")에만 보인다 — 그리기/오버레이는
// 이미 CH-4 자체 locked 필드가 있고(drawings/model.ts) 그 UI는 별도 리프 소관이라
// 여기서 다시 만들지 않는다(objectTree.ts sortByPersistedOrder 주석과 동일 결정).
//
// statusLine/dataWindow(legend/statusLine.ts, legend/dataWindow.ts)는 이
// 컴포넌트에서 재사용하지 않는다: 두 모듈 모두 타입 목적으로 "../core/klinecharts"를
// 가져오는데, 그 파일은 vendor/klinecharts를 값으로도 재수출해서(런타임 코드) apps/web의
// verbatimModuleSyntax/erasableSyntaxOnly tsconfig 아래서 vendor 소스 전체가 함께
// 타입체크되어 tsc -b가 깨진다(ChartPage.tsx 상단 주석의 배럴 문제와 같은 부류 —
// 여기서는 배럴이 아니라 statusLine.ts/dataWindow.ts 자체가 그 경로를 문다). 크로스헤어에
// 동기화된 시각 표시(DoD)는 ChartPanes.tsx가 legend/crosshairSync.ts만으로 직접
// 충족한다.
import type { ObjectTreeEntry } from "@aios/chart-engine/src/legend/objectTree";

export interface ChartLegendProps {
  readonly objectTree: readonly ObjectTreeEntry[];
  readonly onToggleVisible: (id: string) => void;
  /** CH-16b: `toIndex` is this entry's position within `objectTree` after the move (moveEntry.ts semantics). */
  readonly onMoveEntry: (id: string, toIndex: number) => void;
  /** CH-16b: only ever called for `kind === "indicator"` entries — see file header. */
  readonly onToggleLocked: (id: string) => void;
}

export function ChartLegend({ objectTree, onToggleVisible, onMoveEntry, onToggleLocked }: ChartLegendProps) {
  return (
    <section aria-label="차트 레전드" className="space-y-2 rounded-lg border border-border p-3 text-xs" data-testid="chart-legend">
      <ul aria-label="지표 트리" data-testid="chart-legend-objecttree" className="space-y-1">
        {objectTree.map((entry, index) => (
          <li key={entry.id} className="flex items-center justify-between gap-2">
            <span>
              {entry.kind === "indicator" ? "▤" : "✎"} {entry.name}{" "}
              <span className="text-fg-muted">({entry.paneId})</span>
            </span>
            <span className="flex items-center gap-1">
              <button
                type="button"
                aria-label={`순서 위로 ${entry.name}`}
                disabled={index === 0}
                onClick={() => onMoveEntry(entry.id, index - 1)}
              >
                ▲
              </button>
              <button
                type="button"
                aria-label={`순서 아래로 ${entry.name}`}
                disabled={index === objectTree.length - 1}
                onClick={() => onMoveEntry(entry.id, index + 1)}
              >
                ▼
              </button>
              {entry.kind === "indicator" && (
                <button
                  type="button"
                  aria-pressed={entry.locked}
                  aria-label={`잠금 전환 ${entry.name}`}
                  onClick={() => onToggleLocked(entry.id)}
                >
                  {entry.locked ? "🔒" : "🔓"}
                </button>
              )}
              <button
                type="button"
                aria-pressed={entry.visible}
                aria-label={`표시 전환 ${entry.name}`}
                onClick={() => onToggleVisible(entry.id)}
              >
                {entry.visible ? "👁" : "🚫"}
              </button>
            </span>
          </li>
        ))}
        {objectTree.length === 0 && <li className="text-fg-muted">지표·그리기가 없습니다.</li>}
      </ul>
    </section>
  );
}
