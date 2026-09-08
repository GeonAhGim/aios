// CH-14/CH-15b — ChartPanes.tsx가 rects.map()에서 그리는 페인 한 칸. 서프페이스·
// SVG plot 오버레이·statusLine/제거 버튼 스트립까지 한 페인의 DOM을 그대로 옮긴
// 것으로, 이 컴포넌트 자체는 새 렌더링 개념을 도입하지 않는다(순수 이동).
import type { ReactNode } from "react";
import { Alert } from "@aios/ui-web";
import { PLOT_ERROR_REASONS, type PlotLayerResult } from "./ChartPlotLayer";
import { SURFACE_WIDTH_PX } from "./chartPanesModel";

export interface ChartPaneRowProps {
  readonly paneId: string;
  readonly rectHeight: number;
  readonly heightRatio: number;
  readonly isMain: boolean;
  /** Rendered only when `isMain` — the wired candle renderer (CandlestickChart via useVisibleCandles.ts). */
  readonly mainContent: ReactNode;
  /** Sub-pane label (overlayIdFromSubPaneId(paneId)) — unused when `isMain`. */
  readonly subLabel: string;
  readonly plotLayer: PlotLayerResult;
  readonly crosshairTimeMs: number | null;
  readonly onMouseMove: (clientX: number) => void;
  readonly onMouseLeave: () => void;
  /** Sub-pane "×" handler — absent for the main pane. */
  readonly onRemoveSubOverlay?: () => void;
}

export function ChartPaneRow({
  paneId,
  rectHeight,
  heightRatio,
  isMain,
  mainContent,
  subLabel,
  plotLayer,
  crosshairTimeMs,
  onMouseMove,
  onMouseLeave,
  onRemoveSubOverlay,
}: ChartPaneRowProps) {
  return (
    <div
      data-testid={`chart-pane-${paneId}`}
      className="relative border-b border-border last:border-b-0"
      style={{ height: rectHeight }}
    >
      <div
        data-testid={`chart-pane-surface-${paneId}`}
        className="h-full w-full"
        onMouseMove={(e) => onMouseMove(e.clientX)}
        onMouseLeave={onMouseLeave}
      >
        {isMain ? mainContent : <p className="p-2 text-xs text-fg-muted">서브패널 · {subLabel}</p>}
      </div>
      <svg
        className="pointer-events-none absolute inset-0"
        width={SURFACE_WIDTH_PX}
        height={rectHeight}
        data-testid={`chart-pane-plot-${paneId}`}
      >
        {plotLayer.nodes}
      </svg>
      {plotLayer.issues.length > 0 && (
        <div data-testid={`chart-pane-plot-error-${paneId}`}>
          <Alert tone="warning">
            {plotLayer.issues.map((issue) => (
              <p key={`${issue.overlayId}:${issue.output}`}>
                지표 표시 실패: {issue.overlayId}.{issue.output} — {PLOT_ERROR_REASONS[issue.code]} ({issue.code})
              </p>
            ))}
          </Alert>
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between bg-surface/80 px-2 py-0.5 text-[11px] text-fg-secondary">
        <span data-testid={`chart-pane-ratio-${paneId}`}>{heightRatio.toFixed(6)}</span>
        <span data-testid={`chart-pane-statusline-${paneId}`}>
          {crosshairTimeMs !== null ? new Date(crosshairTimeMs).toISOString() : "--"}
        </span>
        {!isMain && (
          <button type="button" className="pointer-events-auto" aria-label={`서브패널 제거 ${subLabel}`} onClick={onRemoveSubOverlay}>
            ×
          </button>
        )}
      </div>
    </div>
  );
}
