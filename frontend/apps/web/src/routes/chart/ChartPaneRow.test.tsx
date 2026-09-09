import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlotLayerResult } from "./ChartPlotLayer";
import { ChartPaneRow } from "./ChartPaneRow";

afterEach(() => cleanup());

const EMPTY_PLOT: PlotLayerResult = { nodes: [], issues: [] };

function baseProps() {
  return {
    paneId: "main",
    rectHeight: 240,
    heightRatio: 0.6,
    isMain: true,
    mainContent: <div>CANDLE_CHART</div>,
    subLabel: "RSI",
    plotLayer: EMPTY_PLOT,
    crosshairTimeMs: null as number | null,
    onMouseMove: vi.fn(),
    onMouseLeave: vi.fn(),
    onRemoveSubOverlay: undefined as (() => void) | undefined,
  };
}

describe("ChartPaneRow 정상 렌더(메인 페인)", () => {
  it("isMain이면 mainContent를 그대로 보여주고, 서브패널 제거 버튼은 없다", () => {
    render(<ChartPaneRow {...baseProps()} crosshairTimeMs={1_700_000_000_000} />);

    expect(screen.getByText("CANDLE_CHART")).toBeInTheDocument();
    expect(screen.queryByText(/서브패널/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("chart-pane-statusline-main")).toHaveTextContent(
      new Date(1_700_000_000_000).toISOString(),
    );
    expect(screen.getByTestId("chart-pane-ratio-main")).toHaveTextContent("0.600000");
  });
});

describe("ChartPaneRow 경계 입력(서브패널·크로스헤어 없음)", () => {
  it("isMain=false면 subLabel 안내문과 제거 버튼을 보여주고, crosshairTimeMs가 null이면 '--'를 보여준다", () => {
    const onRemoveSubOverlay = vi.fn();
    render(
      <ChartPaneRow
        {...baseProps()}
        paneId="sub-1"
        isMain={false}
        subLabel="RSI"
        onRemoveSubOverlay={onRemoveSubOverlay}
      />,
    );

    expect(screen.getByText("서브패널 · RSI")).toBeInTheDocument();
    expect(screen.getByTestId("chart-pane-statusline-sub-1")).toHaveTextContent("--");

    fireEvent.click(screen.getByRole("button", { name: "서브패널 제거 RSI" }));
    expect(onRemoveSubOverlay).toHaveBeenCalledTimes(1);
  });
});

describe("ChartPaneRow 에러 표면(plotLayer.issues)", () => {
  it("issues가 있으면 원본 코드 대신 PLOT_ERROR_REASONS로 매핑된 문구를 보여준다", () => {
    const plotLayer: PlotLayerResult = {
      nodes: [],
      issues: [{ overlayId: "rsi-14", output: "value", code: "PLOT_RENDER_SERIES_MISSING" }],
    };
    render(<ChartPaneRow {...baseProps()} plotLayer={plotLayer} />);

    expect(
      screen.getByText("지표 표시 실패: rsi-14.value — 지표 시리즈 데이터가 없습니다. (PLOT_RENDER_SERIES_MISSING)"),
    ).toBeInTheDocument();
  });

  it("issues가 없으면 에러 배너를 렌더하지 않는다", () => {
    render(<ChartPaneRow {...baseProps()} />);

    expect(screen.queryByText(/지표 표시 실패/)).not.toBeInTheDocument();
  });
});
