import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartToolbar } from "./ChartToolbar";

afterEach(cleanup);

function baseProps() {
  return {
    venue: "BITGET" as const,
    onVenueChange: vi.fn(),
    timeframe: "1h" as const,
    onTimeframeChange: vi.fn(),
    drawingTool: null,
    onDrawingToolChange: vi.fn(),
    onAddDrawing: vi.fn(),
    addDrawingDisabled: true,
    replayStatus: "paused" as const,
    replaySpeed: 1,
    replayDisabled: false,
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onStep: vi.fn(),
    onSpeedChange: vi.fn(),
  };
}

describe("ChartToolbar", () => {
  it("그리기 도구 버튼을 누르면 선택되고, 다시 누르면 해제된다", () => {
    const onDrawingToolChange = vi.fn();
    const { rerender } = render(<ChartToolbar {...baseProps()} onDrawingToolChange={onDrawingToolChange} />);

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    expect(onDrawingToolChange).toHaveBeenCalledWith("trendline");

    rerender(<ChartToolbar {...baseProps()} drawingTool="trendline" onDrawingToolChange={onDrawingToolChange} />);
    expect(screen.getByRole("button", { name: "추세선" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    expect(onDrawingToolChange).toHaveBeenLastCalledWith(null);
  });

  it("negative: 그리기 도구가 없으면 추가 버튼이 비활성화된다", () => {
    render(<ChartToolbar {...baseProps()} addDrawingDisabled />);
    expect(screen.getByRole("button", { name: "그리기 추가" })).toBeDisabled();
  });

  it("도구 그룹 안에서 화살표 키로 포커스를 이동한다(roving tabindex)", () => {
    render(<ChartToolbar {...baseProps()} />);
    const buttons = [
      screen.getByRole("button", { name: "추세선" }),
      screen.getByRole("button", { name: "수평선" }),
    ];
    expect(buttons[0]).toHaveAttribute("tabindex", "0");
    expect(buttons[1]).toHaveAttribute("tabindex", "-1");

    buttons[0]!.focus();
    fireEvent.keyDown(screen.getByRole("toolbar"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(buttons[1]);
  });

  it("재생 상태에 따라 재생/일시정지 버튼 라벨과 콜백이 바뀐다", () => {
    const onPlay = vi.fn();
    const onPause = vi.fn();
    const { rerender } = render(<ChartToolbar {...baseProps()} onPlay={onPlay} onPause={onPause} />);

    fireEvent.click(screen.getByRole("button", { name: "재생" }));
    expect(onPlay).toHaveBeenCalledTimes(1);

    rerender(<ChartToolbar {...baseProps()} replayStatus="playing" onPlay={onPlay} onPause={onPause} />);
    fireEvent.click(screen.getByRole("button", { name: "일시정지" }));
    expect(onPause).toHaveBeenCalledTimes(1);
  });

  it("negative: 재생 비활성화 시 이전/다음/재생 버튼이 모두 비활성화된다", () => {
    render(<ChartToolbar {...baseProps()} replayDisabled />);
    expect(screen.getByRole("button", { name: "이전 봉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 봉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "재생" })).toBeDisabled();
  });

  it("거래소·타임프레임·배속 select 값이 바뀌면 각 콜백이 호출된다", () => {
    const onVenueChange = vi.fn();
    const onTimeframeChange = vi.fn();
    const onSpeedChange = vi.fn();
    render(
      <ChartToolbar
        {...baseProps()}
        onVenueChange={onVenueChange}
        onTimeframeChange={onTimeframeChange}
        onSpeedChange={onSpeedChange}
      />,
    );

    const [venueSelect, timeframeSelect, speedSelect] = screen.getAllByRole("combobox");
    fireEvent.change(venueSelect!, { target: { value: "KIS_KRX" } });
    expect(onVenueChange).toHaveBeenCalledWith("KIS_KRX");

    fireEvent.change(timeframeSelect!, { target: { value: "5m" } });
    expect(onTimeframeChange).toHaveBeenCalledWith("5m");

    fireEvent.change(speedSelect!, { target: { value: "4" } });
    expect(onSpeedChange).toHaveBeenCalledWith(4);
  });
});
