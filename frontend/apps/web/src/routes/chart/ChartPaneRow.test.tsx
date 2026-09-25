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

// task-5419: this describe block used to also assert a 100ms wall-clock
// upper bound on the render below (in the same `it`). That assertion failed
// on the shared GitHub-hosted Actions runner (run 35795467536, f0fbb4ba:
// expected 348.2 to be less than 100) with no functional regression in
// ChartPaneRow -- a fixed-ms budget in a unit test can't tell "the component
// got slower" apart from "the runner is contended right now" (same root
// cause task-4973/4982 fixed for packages/api-client's idempotency-scan
// test). Per that precedent, the fix is NOT to raise the threshold or
// skip/retry the test -- it is to keep the functional assertion here (all
// 1000+ nodes render, none dropped/misrendered) and move the performance
// requirement to its own host-load-normalized bench:
// ../../../bench/chartPaneRowRatchet.mjs (ratio-to-calib gate, decided
// against a fixed, side-effect-free calib workload measured in the same
// process immediately around the real render -- see that module's docstring
// for the measured calibMs/renderMs/ratio data) run via
// `npm run bench:chart-pane-row --workspace=apps/web`, wired into CI as its
// own step alongside `bench:density`/`bench:idempotency-scan` rather than
// inside `vitest run`.
describe("ChartPaneRow 고밀도 렌더(CH-14, 노드 1000개+)", () => {
  it("매우 큰 plotLayer 결과(노드 1000개+)도 전부 렌더되고 결과 집합이 일치한다", () => {
    // ChartPaneRow.tsx는 plotLayer.nodes를 <svg>{plotLayer.nodes}</svg>로 그대로 렌더한다
    // (PlotLayerResult.nodes: readonly ReactNode[]). 여기서 {type,x,y,color} 같은 순수 데이터
    // 객체를 넣으면 React가 reconcileChildren 중
    // "Objects are not valid as a React child"로 크래시한다 — vitest test:coverage가
    // node_modules/react-dom/cjs/react-dom-client.development.js의 reconcileChildren/
    // beginWork/performUnitOfWork 스택으로 반복 실패한 원인(esc-ci-38c28e2a2177/
    // b4851ec32970/cec0cf9fd1aa, task-4072). 실제 렌더 트리와 같은 SVG 엘리먼트를 써야 한다.
    const NODE_COUNT = 1000;
    const hugeNodes = Array.from({ length: NODE_COUNT }, (_, i) => (
      <circle key={i} cx={i} cy={100 + Math.sin(i) * 50} r={1} fill="#1f77b4" />
    ));
    const plotLayer: PlotLayerResult = { nodes: hugeNodes, issues: [] };

    const { container } = render(<ChartPaneRow {...baseProps()} plotLayer={plotLayer} />);

    expect(screen.getByText("CANDLE_CHART")).toBeInTheDocument();
    const renderedCircles = container.querySelectorAll("circle");
    expect(renderedCircles).toHaveLength(NODE_COUNT);
    // Result set equality, not just count: every input node's (cx, cy) pair must
    // survive the render untouched -- catches truncation/reordering/dedup bugs
    // that a length-only check would miss.
    const renderedPairs = Array.from(renderedCircles).map((el) => `${el.getAttribute("cx")},${el.getAttribute("cy")}`);
    const expectedPairs = hugeNodes.map((_, i) => `${i},${100 + Math.sin(i) * 50}`);
    expect(renderedPairs).toEqual(expectedPairs);
  });
});
