// 배럴(@aios/chart-engine index.ts)은 core/klinechartsBackend를 통해 vendor
// klinecharts 포크까지 재수출한다 — 그 vendor 코드는 chart-engine 자신의
// 완화된 tsconfig(strictPropertyInitialization:false 등)에서만 통과하고
// apps/web의 엄격한 tsconfig로는 tsc -b가 깨진다. CH-4가 실제로 필요한 건
// vendor에 의존하지 않는 drawings/model뿐이라 그 서브모듈만 직접 불러온다.
import type { DrawingKind } from "@aios/chart-engine/src/drawings/model";
import type { ReplayStatus } from "@aios/chart-engine/src/replay/replayController";
import type { Timeframe, Venue } from "@aios/shared-types";
import { Button, Field, Select } from "@aios/ui-web";
import { useState } from "react";
import { AlertFromChart } from "./AlertFromChart";
import { DrawingReplayToolbar } from "./DrawingReplayToolbar";
import { LayoutPanelControls } from "./LayoutPanelControls";
import type { ChartLayoutSaveStatus } from "./useChartLayout";

// CH-6b: CH-8(task-1593) 레이아웃 CRUD의 화면 배선. 실제 복원·저장·충돌 판정은
// useChartLayout이 전담하고, 여기서는 그 결과(panels/activePanelId/saveStatus 등)를
// 받아 버튼·탭으로만 노출한다 — fetch를 이 파일에서 직접 부르지 않는다(decision).

// CH-6a: 조립 화면의 툴바. 실제 렌더링·값 계산 없이 로컬 상태만 위아래로
// 오간다 — venue/timeframe은 ChartPage의 fetch 키를, 그리기 도구·재생
// 컨트롤은 각각 chart-engine CH-4(drawings/tools)·CH-7(replayController)
// 소비 지점을 그대로 노출한다.

const VENUES: readonly Venue[] = ["BITGET", "KIS_KRX", "KIS_US"];
const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const SPEEDS: readonly number[] = [0.5, 1, 2, 4, 8];

export interface ChartLayoutPanelTab {
  readonly id: string;
  readonly label: string;
}

export interface ChartLayoutControls {
  readonly name: string;
  readonly onNameChange: (name: string) => void;
  readonly onSave: () => void;
  readonly onDelete: () => void;
  readonly saveStatus: ChartLayoutSaveStatus;
  readonly onReload: () => void;
  readonly panels: readonly ChartLayoutPanelTab[];
  readonly activePanelId: string | null;
  readonly onSelectPanel: (id: string) => void;
  readonly onAddPanel: () => void;
  readonly onRemovePanel: () => void;
  readonly isWatchlisted: boolean;
  readonly onToggleWatchlist: () => void;
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
  // CH-9: AlertFromChart(가격/지표 알림 생성 다이얼로그)의 차트 컨텍스트 — 이
  // 다이얼로그를 여기서 직접 마운트해 "구현됨=작동함"을 이 컴포넌트의 테스트만으로
  // 증명한다(I-10, ChartPage를 거치지 않는 배선 증명).
  instrumentId: string;
  currentClose: number | null;
  selectedIndicatorIds: readonly string[];
  // CH-6b/CH-8: 서버 저장·복원 배선(레이아웃 이름·저장·삭제·패널 탭·관심목록·충돌 안내).
  layout: ChartLayoutControls;
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
  instrumentId,
  currentClose,
  selectedIndicatorIds,
  layout,
}: ChartToolbarProps) {
  const [alertDialogOpen, setAlertDialogOpen] = useState(false);

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

      <DrawingReplayToolbar
        drawingTool={drawingTool}
        onDrawingToolChange={onDrawingToolChange}
        onAddDrawing={onAddDrawing}
        addDrawingDisabled={addDrawingDisabled}
        replayStatus={replayStatus}
        replayDisabled={replayDisabled}
        onPlay={onPlay}
        onPause={onPause}
        onStep={onStep}
      />

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

      <Button type="button" variant="secondary" size="sm" onClick={() => setAlertDialogOpen(true)}>
        알림
      </Button>
      <AlertFromChart
        isOpen={alertDialogOpen}
        onClose={() => setAlertDialogOpen(false)}
        venue={venue}
        instrumentId={instrumentId}
        timeframe={timeframe}
        currentClose={currentClose}
        selectedIndicatorIds={selectedIndicatorIds}
      />

      <LayoutPanelControls layout={layout} />
    </div>
  );
}
