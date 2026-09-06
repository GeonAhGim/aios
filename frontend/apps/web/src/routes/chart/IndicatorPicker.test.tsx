import "@testing-library/jest-dom/vitest";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorPicker } from "./IndicatorPicker";

afterEach(cleanup);

const AVAILABLE = createDefaultOverlayRegistry().list();

describe("IndicatorPicker", () => {
  it("트리거 버튼을 누르면 지표 목록이 listbox로 열린다", () => {
    render(<IndicatorPicker available={AVAILABLE} selectedIds={[]} onToggle={vi.fn()} />);

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    expect(screen.getByRole("listbox", { name: "지표 목록" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(AVAILABLE.length);
  });

  it("옵션을 클릭하면 onToggle이 해당 id로 호출된다", () => {
    const onToggle = vi.fn();
    render(<IndicatorPicker available={AVAILABLE} selectedIds={[]} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    fireEvent.click(screen.getByRole("option", { name: /SMA/ }));
    expect(onToggle).toHaveBeenCalledWith("SMA");
  });

  it("선택된 지표는 aria-selected=true이고 선택 칩으로 표시된다", () => {
    render(<IndicatorPicker available={AVAILABLE} selectedIds={["SMA"]} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    expect(screen.getByRole("option", { name: /SMA/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "SMA ✕" })).toBeInTheDocument();
  });

  it("ArrowDown/Enter로 키보드만으로 지표를 토글할 수 있다", () => {
    const onToggle = vi.fn();
    render(<IndicatorPicker available={AVAILABLE} selectedIds={[]} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onToggle).toHaveBeenCalledWith(AVAILABLE[1]!.id);
  });

  it("Escape를 누르면 목록이 닫히고 트리거로 포커스가 돌아간다", () => {
    render(<IndicatorPicker available={AVAILABLE} selectedIds={[]} onToggle={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: /지표 선택/ });
    fireEvent.click(trigger);

    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
  });

  it("negative: 지표가 없으면 안내 문구만 보여준다", () => {
    render(<IndicatorPicker available={[]} selectedIds={[]} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    expect(screen.getByText("지표가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });
});
