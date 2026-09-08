import { DRAWING_KINDS, type DrawingKind } from "@aios/chart-engine/src/drawings/model";
import type { ReplayStatus } from "@aios/chart-engine/src/replay/replayController";
import { Button } from "@aios/ui-web";
import { type ButtonSpec, useRovingToolbar } from "./useRovingToolbar";

const DRAWING_LABELS: Record<DrawingKind, string> = {
  trendline: "추세선",
  "horizontal-line": "수평선",
  "vertical-line": "수직선",
  rectangle: "사각형",
  fibonacci: "피보나치",
};

interface DrawingReplayToolbarProps {
  drawingTool: DrawingKind | null;
  onDrawingToolChange: (tool: DrawingKind | null) => void;
  onAddDrawing: () => void;
  addDrawingDisabled: boolean;
  replayStatus: ReplayStatus;
  replayDisabled: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStep: (delta: 1 | -1) => void;
}

export function DrawingReplayToolbar({
  drawingTool,
  onDrawingToolChange,
  onAddDrawing,
  addDrawingDisabled,
  replayStatus,
  replayDisabled,
  onPlay,
  onPause,
  onStep,
}: DrawingReplayToolbarProps) {
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
  );
}
