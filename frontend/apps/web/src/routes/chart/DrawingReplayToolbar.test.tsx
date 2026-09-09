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
