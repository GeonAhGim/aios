import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartToolbar } from "./ChartToolbar";

const createAlertMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useCreateAlert: () => ({ mutateAsync: createAlertMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  createAlertMutateAsync.mockReset();
});

function baseLayout() {
  return {
    name: "기본 레이아웃",
    onNameChange: vi.fn(),
    onSave: vi.fn(),
    onDelete: vi.fn(),
    saveStatus: "idle" as const,
    onReload: vi.fn(),
    panels: [{ id: "panel-1", label: "BTCUSDT · 1h" }],
    activePanelId: "panel-1",
    onSelectPanel: vi.fn(),
    onAddPanel: vi.fn(),
    onRemovePanel: vi.fn(),
    isWatchlisted: false,
    onToggleWatchlist: vi.fn(),
  };
}

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
    instrumentId: "BTCUSDT",
    currentClose: 50200,
    selectedIndicatorIds: [] as readonly string[],
    layout: baseLayout(),
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

// CH-9/I-10: AlertFromChart는 ChartToolbar가 직접 마운트한다(ChartPage를 거치지 않음) —
// 이 배선이 실제로 작동함을 이 테스트가 증명한다. mock은 useCreateAlert 하나뿐이라
// AlertFromChart 자체 로직(에러 판정 등)은 재구현하지 않고 실제 코드를 그대로 태운다.
describe("ChartToolbar — CH-9 알림 다이얼로그 배선", () => {
  it("'알림' 버튼을 누르면 다이얼로그가 열리고 차트 컨텍스트(심볼·현재가)가 프리필된다", () => {
    render(
      <MemoryRouter>
        <ChartToolbar {...baseProps()} instrumentId="BTCUSDT" currentClose={50200} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "알림" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent("BTCUSDT");
    expect(dialog).toHaveTextContent("50200");
    expect(screen.getByLabelText("임계값")).toHaveValue(50200);
  });

  it("negative: Esc를 누르면 다이얼로그가 닫힌다", () => {
    render(
      <MemoryRouter>
        <ChartToolbar {...baseProps()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "알림" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("제출하면 서버 계약 필드명 그대로 AlertsPage와 같은 useCreateAlert를 호출한다", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 7 });
    render(
      <MemoryRouter>
        <ChartToolbar {...baseProps()} instrumentId="BTCUSDT" currentClose={50200} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "알림" }));
    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(createAlertMutateAsync).toHaveBeenCalledWith({
        exchange: "bitget",
        symbol: "BTCUSDT",
        timeframe: "1h",
        indicator: "SMA",
        params: { timeperiod: 1 },
        operator: ">",
        threshold: 50200,
      }),
    );
    expect(await screen.findByText(/알림 목록에서 확인/)).toBeInTheDocument();
  });
});
