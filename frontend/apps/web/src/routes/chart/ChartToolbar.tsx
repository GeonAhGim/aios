// 배럴(@aios/chart-engine index.ts)은 core/klinechartsBackend를 통해 vendor
// klinecharts 포크까지 재수출한다 — 그 vendor 코드는 chart-engine 자신의
// 완화된 tsconfig(strictPropertyInitialization:false 등)에서만 통과하고
// apps/web의 엄격한 tsconfig로는 tsc -b가 깨진다. CH-4가 실제로 필요한 건
// vendor에 의존하지 않는 drawings/model뿐이라 그 서브모듈만 직접 불러온다.
import { DRAWING_KINDS, type DrawingKind } from "@aios/chart-engine/src/drawings/model";
import type { ReplayStatus } from "@aios/chart-engine/src/replay/replayController";
import type { Timeframe, Venue } from "@aios/shared-types";
import { Button, Field, Select } from "@aios/ui-web";
import type { KeyboardEvent } from "react";
import { useRef, useState } from "react";

// CH-6a: 조립 화면의 툴바. 실제 렌더링·값 계산 없이 로컬 상태만 위아래로
// 오간다 — venue/timeframe은 ChartPage의 fetch 키를, 그리기 도구·재생
// 컨트롤은 각각 chart-engine CH-4(drawings/tools)·CH-7(replayController)
// 소비 지점을 그대로 노출한다.

const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const SPEEDS: readonly number[] = [0.5, 1, 2, 4, 8];

const DRAWING_LABELS: Record<DrawingKind, string> = {
  trendline: "추세선",
  "horizontal-line": "수평선",
  "vertical-line": "수직선",
  rectangle: "사각형",
  fibonacci: "피보나치",
};

interface ButtonSpec {
  readonly id: string;
  readonly disabled: boolean;
}

interface ChartToolbarProps {
  venue: Venue;
  onVenueChange: (venue: Venue) => void;
  timeframe: Timeframe;
  onTimeframeChange: (timeframe: Timeframe) => void;
  drawingTool: DrawingKind | null;
  onDrawingToolChange: (tool: DrawingKind | null) => void;
  onAddDrawing: () => void;
  addDrawingDisabled: boolean;
  replayStatus: ReplayStatus;
  replaySpeed: number;
  replayDisabled: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStep: (delta: 1 | -1) => void;
  onSpeedChange: (speed: number) => void;
}

/** WAI-ARIA toolbar 패턴: 그룹 내 버튼은 하나만 tabIndex=0(roving), 화살표로 이동한다. */
function useRovingToolbar(buttons: readonly ButtonSpec[]) {
  const enabledIds = buttons.filter((b) => !b.disabled).map((b) => b.id);
  const [activeId, setActiveId] = useState<string>(enabledIds[0] ?? "");
  const groupRef = useRef<HTMLDivElement>(null);

  function tabIndexFor(id: string): 0 | -1 {
    if (!enabledIds.includes(id)) return -1;
    const active = enabledIds.includes(activeId) ? activeId : enabledIds[0];
    return id === active ? 0 : -1;
  }

  function onFocusButton(id: string): void {
    if (enabledIds.includes(id)) setActiveId(id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const container = groupRef.current;
    if (!container) return;
    const focusable = Array.from(container.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    if (focusable.length === 0) return;
    const current = focusable.indexOf(document.activeElement as HTMLButtonElement);
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1 + focusable.length) % focusable.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + focusable.length) % focusable.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = focusable.length - 1;
    else return;
    event.preventDefault();
    focusable[next]?.focus();
  }

  return { groupRef, tabIndexFor, onFocusButton, onKeyDown };
}

export function ChartToolbar({
  venue,
  onVenueChange,
  timeframe,
  onTimeframeChange,
  drawingTool,
  onDrawingToolChange,
  onAddDrawing,
  addDrawingDisabled,
  replayStatus,
  replaySpeed,
  replayDisabled,
  onPlay,
  onPause,
  onStep,
  onSpeedChange,
}: ChartToolbarProps) {
  const buttons: ButtonSpec[] = [
    ...DRAWING_KINDS.map((kind) => ({ id: `tool-${kind}`, disabled: false })),
    { id: "add-drawing", disabled: addDrawingDisabled },
    { id: "replay-step-back", disabled: replayDisabled },
    { id: "replay-toggle", disabled: replayDisabled },
    { id: "replay-step-forward", disabled: replayDisabled },
  ];
  const { groupRef, tabIndexFor, onFocusButton, onKeyDown } = useRovingToolbar(buttons);

  function toggleTool(kind: DrawingKind): void {
    onDrawingToolChange(drawingTool === kind ? null : kind);
  }

  return (
    <div className="flex flex-wrap items-end gap-3" data-testid="chart-toolbar">
      <Field label="거래소">
        <Select value={venue} onChange={(e) => onVenueChange(e.target.value as Venue)}>
          {VENUES.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="타임프레임">
        <Select value={timeframe} onChange={(e) => onTimeframeChange(e.target.value as Timeframe)}>
          {TIMEFRAMES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
      </Field>

      <div
        ref={groupRef}
        role="toolbar"
        aria-label="그리기·재생 도구"
        className="flex flex-wrap items-center gap-2"
        onKeyDown={onKeyDown}
      >
        {DRAWING_KINDS.map((kind) => {
          const id = `tool-${kind}`;
          return (
            <Button
              key={kind}
              id={id}
              type="button"
              variant={drawingTool === kind ? "primary" : "secondary"}
              size="sm"
              aria-pressed={drawingTool === kind}
              tabIndex={tabIndexFor(id)}
              onFocus={() => onFocusButton(id)}
              onClick={() => toggleTool(kind)}
            >
              {DRAWING_LABELS[kind]}
            </Button>
          );
        })}
        <Button
          id="add-drawing"
          type="button"
          variant="secondary"
          size="sm"
          disabled={addDrawingDisabled}
          tabIndex={tabIndexFor("add-drawing")}
          onFocus={() => onFocusButton("add-drawing")}
          onClick={onAddDrawing}
        >
          그리기 추가
        </Button>

        <Button
          id="replay-step-back"
          type="button"
          variant="ghost"
          size="sm"
          aria-label="이전 봉"
          disabled={replayDisabled}
          tabIndex={tabIndexFor("replay-step-back")}
          onFocus={() => onFocusButton("replay-step-back")}
          onClick={() => onStep(-1)}
        >
          ◀
        </Button>
        <Button
          id="replay-toggle"
          type="button"
          variant="secondary"
          size="sm"
          aria-pressed={replayStatus === "playing"}
          disabled={replayDisabled}
          tabIndex={tabIndexFor("replay-toggle")}
          onFocus={() => onFocusButton("replay-toggle")}
          onClick={replayStatus === "playing" ? onPause : onPlay}
        >
          {replayStatus === "playing" ? "일시정지" : "재생"}
        </Button>
        <Button
          id="replay-step-forward"
          type="button"
          variant="ghost"
          size="sm"
          aria-label="다음 봉"
          disabled={replayDisabled}
          tabIndex={tabIndexFor("replay-step-forward")}
          onFocus={() => onFocusButton("replay-step-forward")}
          onClick={() => onStep(1)}
        >
          ▶
        </Button>
      </div>

      <Field label="배속">
        <Select
          value={String(replaySpeed)}
          disabled={replayDisabled}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
        >
          {SPEEDS.map((speed) => (
            <option key={speed} value={speed}>
              {speed}x
            </option>
          ))}
        </Select>
      </Field>
    </div>
  );
}
