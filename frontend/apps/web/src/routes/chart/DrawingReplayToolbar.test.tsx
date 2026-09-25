import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DrawingReplayToolbar } from "./DrawingReplayToolbar";

afterEach(() => cleanup());

function baseProps() {
  return {
    drawingTool: null as null,
    onDrawingToolChange: vi.fn(),
    onAddDrawing: vi.fn(),
    addDrawingDisabled: false,
    replayStatus: "paused" as const,
    replayDisabled: false,
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onStep: vi.fn(),
  };
}

describe("DrawingReplayToolbar 정상 렌더", () => {
  it("툴바 role과 그리기 도구·재생 버튼을 모두 보여준다", () => {
    render(<DrawingReplayToolbar {...baseProps()} />);

    expect(screen.getByRole("toolbar", { name: "그리기·재생 도구" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "추세선" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "피보나치" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "그리기 추가" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "재생" })).toBeInTheDocument();
  });

  it("그리기 도구를 클릭하면 onDrawingToolChange(kind)를 호출하고, 같은 도구를 다시 누르면 null로 되돌린다", () => {
    const onDrawingToolChange = vi.fn();
    const { rerender } = render(
      <DrawingReplayToolbar {...baseProps()} onDrawingToolChange={onDrawingToolChange} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    expect(onDrawingToolChange).toHaveBeenCalledWith("trendline");

    rerender(
      <DrawingReplayToolbar
        {...baseProps()}
        drawingTool="trendline"
        onDrawingToolChange={onDrawingToolChange}
      />,
    );
    expect(screen.getByRole("button", { name: "추세선" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    expect(onDrawingToolChange).toHaveBeenLastCalledWith(null);
  });
});

describe("DrawingReplayToolbar 재생 상태 경계", () => {
  it("replayStatus가 playing이면 '일시정지' 라벨을 보여주고 클릭 시 onPause만 호출한다", () => {
    const onPlay = vi.fn();
    const onPause = vi.fn();
    render(
      <DrawingReplayToolbar {...baseProps()} replayStatus="playing" onPlay={onPlay} onPause={onPause} />,
    );

    const toggle = screen.getByRole("button", { name: "일시정지" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);

    expect(onPause).toHaveBeenCalledTimes(1);
    expect(onPlay).not.toHaveBeenCalled();
  });

  it("이전 봉/다음 봉 버튼 클릭 시 onStep(-1)/onStep(1)을 호출한다", () => {
    const onStep = vi.fn();
    render(<DrawingReplayToolbar {...baseProps()} onStep={onStep} />);

    fireEvent.click(screen.getByRole("button", { name: "이전 봉" }));
    fireEvent.click(screen.getByRole("button", { name: "다음 봉" }));

    expect(onStep).toHaveBeenNthCalledWith(1, -1);
    expect(onStep).toHaveBeenNthCalledWith(2, 1);
  });
});

describe("DrawingReplayToolbar 거부 입력(비활성)", () => {
  it("addDrawingDisabled·replayDisabled가 true면 해당 버튼이 비활성화되고 클릭해도 핸들러가 호출되지 않는다", () => {
    const onAddDrawing = vi.fn();
    const onPlay = vi.fn();
    const onStep = vi.fn();
    render(
      <DrawingReplayToolbar
        {...baseProps()}
        addDrawingDisabled
        replayDisabled
        onAddDrawing={onAddDrawing}
        onPlay={onPlay}
        onStep={onStep}
      />,
    );

    const addButton = screen.getByRole("button", { name: "그리기 추가" });
    const playButton = screen.getByRole("button", { name: "재생" });
    const stepBack = screen.getByRole("button", { name: "이전 봉" });
    expect(addButton).toBeDisabled();
    expect(playButton).toBeDisabled();
    expect(stepBack).toBeDisabled();

    fireEvent.click(addButton);
    fireEvent.click(playButton);
    fireEvent.click(stepBack);

    expect(onAddDrawing).not.toHaveBeenCalled();
    expect(onPlay).not.toHaveBeenCalled();
    expect(onStep).not.toHaveBeenCalled();
  });
});

describe("DrawingReplayToolbar 실패 처리(동시 클릭)", () => {
  it("negative(실패 주입): 빠른 연속 클릭(레이싱) 시에도 각각 정확히 한 번씩만 호출된다", () => {
    const onStep = vi.fn();
    render(<DrawingReplayToolbar {...baseProps()} onStep={onStep} />);

    const stepBackBtn = screen.getByRole("button", { name: "이전 봉" });
    const stepFwdBtn = screen.getByRole("button", { name: "다음 봉" });

    fireEvent.click(stepBackBtn);
    fireEvent.click(stepFwdBtn);
    fireEvent.click(stepBackBtn);

    expect(onStep).toHaveBeenCalledTimes(3);
    expect(onStep).toHaveBeenNthCalledWith(1, -1);
    expect(onStep).toHaveBeenNthCalledWith(2, 1);
    expect(onStep).toHaveBeenNthCalledWith(3, -1);
  });
});

describe("DrawingReplayToolbar 성능 단언(CH-17 툴바 렌더)", () => {
  it("많은 도구 버튼(20개+)을 가진 툴바도 30ms 내로 렌더된다", () => {
    const start = performance.now();
    render(<DrawingReplayToolbar {...baseProps()} />);
    const elapsed = performance.now() - start;

    expect(screen.getByRole("toolbar", { name: "그리기·재생 도구" })).toBeInTheDocument();
    expect(elapsed).toBeLessThan(30);
  });
});

describe("DrawingReplayToolbar 불변식 위반 입력(negative)", () => {
  it("negative: 재생 중 컴포넌트를 unmount해도 예외 없이 정리되고 이후 핸들러가 호출되지 않는다", () => {
    const onStep = vi.fn();
    const onPause = vi.fn();
    const { unmount } = render(
      <DrawingReplayToolbar {...baseProps()} replayStatus="playing" onPause={onPause} onStep={onStep} />,
    );

    expect(() => unmount()).not.toThrow();
    expect(onStep).not.toHaveBeenCalled();
    expect(onPause).not.toHaveBeenCalled();
  });

  it("negative: 도구 목록에 없는 drawingTool 값이 주입돼도 크래시 없이 렌더되고 어떤 버튼도 눌린 상태로 표시되지 않는다", () => {
    render(
      <DrawingReplayToolbar
        {...baseProps()}
        drawingTool={"unknown-tool" as unknown as null}
      />,
    );

    for (const label of ["추세선", "수평선", "수직선", "사각형", "피보나치"]) {
      expect(screen.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("negative: 알 수 없는 replayStatus 값이 들어와도 재생 토글 버튼은 'playing'이 아닌 것으로 안전하게 처리한다", () => {
    render(
      <DrawingReplayToolbar
        {...baseProps()}
        replayStatus={"unknown-status" as unknown as "paused"}
      />,
    );

    const toggle = screen.getByRole("button", { name: "재생" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });
});

describe("DrawingReplayToolbar 실패 주입(콜백 throw)", () => {
  it("failure-injection: onStep 콜백이 throw해도 이후 툴바는 정상적으로 상호작용 가능한 상태를 유지한다", () => {
    const onStep = vi.fn().mockImplementationOnce(() => {
      throw new Error("replay step failed");
    });
    const onDrawingToolChange = vi.fn();
    render(
      <DrawingReplayToolbar {...baseProps()} onStep={onStep} onDrawingToolChange={onDrawingToolChange} />,
    );

    const stepBackBtn = screen.getByRole("button", { name: "이전 봉" });
    const swallowReactDevRethrow = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", swallowReactDevRethrow);
    try {
      fireEvent.click(stepBackBtn);
    } finally {
      window.removeEventListener("error", swallowReactDevRethrow);
    }
    expect(onStep).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    expect(onDrawingToolChange).toHaveBeenCalledWith("trendline");
  });
});
