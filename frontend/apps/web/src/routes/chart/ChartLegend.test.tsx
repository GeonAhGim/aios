import "@testing-library/jest-dom/vitest";
import type { ObjectTreeEntry } from "@aios/chart-engine/src/legend/objectTree";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartLegend } from "./ChartLegend";

afterEach(cleanup);

function entry(overrides: Partial<ObjectTreeEntry> = {}): ObjectTreeEntry {
  return { id: "RSI", kind: "indicator", paneId: "sub-RSI", name: "RSI", visible: true, locked: false, ...overrides };
}

describe("ChartLegend — CH-16 objectTree 조립", () => {
  it("지표·그리기 목록을 표시하고, 표시 전환 버튼은 setEntryVisible 결과의 id를 그대로 넘긴다", () => {
    const onToggleVisible = vi.fn();
    render(
      <ChartLegend
        objectTree={[entry(), entry({ id: "trendline:1", kind: "overlay", name: "trendline:1", paneId: "main" })]}
        onToggleVisible={onToggleVisible}
      />,
    );

    expect(screen.getByRole("button", { name: "표시 전환 RSI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "표시 전환 trendline:1" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "표시 전환 RSI" }));
    expect(onToggleVisible).toHaveBeenCalledWith("RSI");
  });

  it("negative: 목록이 비어 있으면 안내 문구만 보여준다", () => {
    render(<ChartLegend objectTree={[]} onToggleVisible={vi.fn()} />);
    expect(screen.getByText("지표·그리기가 없습니다.")).toBeInTheDocument();
  });
});
