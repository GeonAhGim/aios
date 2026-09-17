import "../../i18n";
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

describe("ChartPaneRow 성능 단언(CH-14 고밀도 렌더)", () => {
  it("고밀도 렌더: 매우 큰 plotLayer 결과(노드 1000개+)도 100ms 내로 렌더된다", () => {
    // ChartPaneRow.tsx는 plotLayer.nodes를 <svg>{plotLayer.nodes}</svg>로 그대로 렌더한다
    // (PlotLayerResult.nodes: readonly ReactNode[]). 여기서 {type,x,y,color} 같은 순수 데이터
    // 객체를 넣으면 React가 reconcileChildren 중
    // "Objects are not valid as a React child"로 크래시한다 — vitest test:coverage가
    // node_modules/react-dom/cjs/react-dom-client.development.js의 reconcileChildren/
    // beginWork/performUnitOfWork 스택으로 반복 실패한 원인(esc-ci-38c28e2a2177/
    // b4851ec32970/cec0cf9fd1aa, task-4072). 실제 렌더 트리와 같은 SVG 엘리먼트를 써야 한다.
    const hugeNodes = Array.from({ length: 1000 }, (_, i) => (
      <circle key={i} cx={i} cy={100 + Math.sin(i) * 50} r={1} fill="#1f77b4" />
    ));
    const plotLayer: PlotLayerResult = { nodes: hugeNodes, issues: [] };

    const start = performance.now();
    render(<ChartPaneRow {...baseProps()} plotLayer={plotLayer} />);
    const elapsed = performance.now() - start;

    expect(screen.getByText("CANDLE_CHART")).toBeInTheDocument();
    expect(elapsed).toBeLessThan(100);
  });
});
