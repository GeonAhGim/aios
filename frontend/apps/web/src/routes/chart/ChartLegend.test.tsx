import "@testing-library/jest-dom/vitest";
import type { ObjectTreeEntry } from "@aios/chart-engine/src/legend/objectTree";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartLegend } from "./ChartLegend";

afterEach(cleanup);

function entry(overrides: Partial<ObjectTreeEntry> = {}): ObjectTreeEntry {
  return { id: "RSI", kind: "indicator", paneId: "sub-RSI", name: "RSI", visible: true, locked: false, ...overrides };
}

function noop() {}

describe("ChartLegend — CH-16 objectTree 조립", () => {
  it("지표·그리기 목록을 표시하고, 표시 전환 버튼은 setEntryVisible 결과의 id를 그대로 넘긴다", () => {
    const onToggleVisible = vi.fn();
    render(
      <ChartLegend
        objectTree={[entry(), entry({ id: "trendline:1", kind: "overlay", name: "trendline:1", paneId: "main" })]}
        onToggleVisible={onToggleVisible}
        onMoveEntry={noop}
        onToggleLocked={noop}
      />,
    );

    expect(screen.getByRole("button", { name: "표시 전환 RSI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "표시 전환 trendline:1" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "표시 전환 RSI" }));
    expect(onToggleVisible).toHaveBeenCalledWith("RSI");
  });

  it("negative: 목록이 비어 있으면 안내 문구만 보여준다", () => {
    render(<ChartLegend objectTree={[]} onToggleVisible={vi.fn()} onMoveEntry={noop} onToggleLocked={noop} />);
    expect(screen.getByText("지표·그리기가 없습니다.")).toBeInTheDocument();
  });
});

describe("ChartLegend — CH-16b 순서·잠금 조작", () => {
  it("순서 아래로 버튼은 moveEntry(id, index+1)와 동등한 toIndex를 넘긴다", () => {
    const onMoveEntry = vi.fn();
    render(
      <ChartLegend
        objectTree={[entry({ id: "SMA", name: "SMA" }), entry({ id: "RSI", name: "RSI" })]}
        onToggleVisible={noop}
        onMoveEntry={onMoveEntry}
        onToggleLocked={noop}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "순서 아래로 SMA" }));
    expect(onMoveEntry).toHaveBeenCalledWith("SMA", 1);
  });

  it("negative: 첫 항목의 위로 버튼과 마지막 항목의 아래로 버튼은 비활성화된다", () => {
    render(
      <ChartLegend
        objectTree={[entry({ id: "SMA", name: "SMA" }), entry({ id: "RSI", name: "RSI" })]}
        onToggleVisible={noop}
        onMoveEntry={noop}
        onToggleLocked={noop}
      />,
    );

    expect(screen.getByRole("button", { name: "순서 위로 SMA" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "순서 아래로 RSI" })).toBeDisabled();
  });

  it("지표는 잠금 토글 버튼을 보여주고 클릭 시 id를 넘긴다", () => {
    const onToggleLocked = vi.fn();
    render(
      <ChartLegend
        objectTree={[entry({ id: "RSI", name: "RSI", locked: false })]}
        onToggleVisible={noop}
        onMoveEntry={noop}
        onToggleLocked={onToggleLocked}
      />,
    );

    const lockButton = screen.getByRole("button", { name: "잠금 전환 RSI" });
    expect(lockButton).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(lockButton);
    expect(onToggleLocked).toHaveBeenCalledWith("RSI");
  });

  it("negative: 그리기/오버레이 항목은 잠금 토글 버튼을 보여주지 않는다(CH-4 자체 locked UI 소관)", () => {
    render(
      <ChartLegend
        objectTree={[entry({ id: "trendline:1", kind: "overlay", name: "trendline:1", paneId: "main" })]}
        onToggleVisible={noop}
        onMoveEntry={noop}
        onToggleLocked={noop}
      />,
    );

    expect(screen.queryByRole("button", { name: "잠금 전환 trendline:1" })).not.toBeInTheDocument();
  });
});
