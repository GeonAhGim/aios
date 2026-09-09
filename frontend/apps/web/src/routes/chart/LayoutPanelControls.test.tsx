import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChartLayoutControls } from "./ChartToolbar";
import { LayoutPanelControls } from "./LayoutPanelControls";

afterEach(() => cleanup());

function baseLayout(overrides: Partial<ChartLayoutControls> = {}): ChartLayoutControls {
  return {
    name: "기본 레이아웃",
    onNameChange: vi.fn(),
    onSave: vi.fn(),
    onDelete: vi.fn(),
    saveStatus: "idle",
    onReload: vi.fn(),
    panels: [
      { id: "p1", label: "패널 1" },
      { id: "p2", label: "패널 2" },
    ],
    activePanelId: "p1",
    onSelectPanel: vi.fn(),
    onAddPanel: vi.fn(),
    onRemovePanel: vi.fn(),
    isWatchlisted: false,
    onToggleWatchlist: vi.fn(),
    ...overrides,
  };
}

describe("LayoutPanelControls 정상 렌더", () => {
  it("패널 탭·저장/삭제/관심목록 버튼을 보여주고, 탭 클릭 시 onSelectPanel을 호출한다", () => {
    const onSelectPanel = vi.fn();
    render(<LayoutPanelControls layout={baseLayout({ onSelectPanel })} />);

    expect(screen.getByRole("tab", { name: "패널 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "패널 2" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("button", { name: "관심목록 추가" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "패널 2" }));
    expect(onSelectPanel).toHaveBeenCalledWith("p2");
  });

  it("isWatchlisted가 true면 '관심목록 제거' 라벨을 보여준다", () => {
    render(<LayoutPanelControls layout={baseLayout({ isWatchlisted: true })} />);

    expect(screen.getByRole("button", { name: "관심목록 제거" })).toBeInTheDocument();
  });
});

describe("LayoutPanelControls 경계 입력(패널 1개·저장 중)", () => {
  it("패널이 1개뿐이면 패널 제거 버튼이 비활성화된다", () => {
    render(
      <LayoutPanelControls
        layout={baseLayout({ panels: [{ id: "p1", label: "패널 1" }], activePanelId: "p1" })}
      />,
    );

    expect(screen.getByRole("button", { name: "패널 제거" })).toBeDisabled();
  });

  it("saveStatus가 saving이면 저장 버튼이 비활성화된다", () => {
    render(<LayoutPanelControls layout={baseLayout({ saveStatus: "saving" })} />);

    expect(screen.getByRole("button", { name: "레이아웃 저장" })).toBeDisabled();
  });
});

describe("LayoutPanelControls 에러·거부 표면(saveStatus)", () => {
  it("saveStatus=conflict면 충돌 안내 문구와 다시 불러오기 버튼을 보여주고 클릭 시 onReload를 호출한다", () => {
    const onReload = vi.fn();
    render(<LayoutPanelControls layout={baseLayout({ saveStatus: "conflict", onReload })} />);

    expect(
      screen.getByText(
        "다른 세션이 먼저 저장했습니다. 자동으로 덮어쓰지 않습니다 — 최신 내용을 다시 불러온 뒤 다시 시도하세요.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("saveStatus=not_found면 삭제 안내 문구를 보여준다", () => {
    render(<LayoutPanelControls layout={baseLayout({ saveStatus: "not_found" })} />);

    expect(screen.getByText("이 레이아웃은 다른 곳에서 삭제되었습니다.")).toBeInTheDocument();
  });

  it("saveStatus=idle이면 경고 배너를 렌더하지 않는다", () => {
    render(<LayoutPanelControls layout={baseLayout({ saveStatus: "idle" })} />);

    expect(screen.queryByText(/다시 불러오기/)).not.toBeInTheDocument();
  });
});
