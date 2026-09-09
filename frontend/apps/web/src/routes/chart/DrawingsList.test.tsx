import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import { DrawingsList } from "./DrawingsList";

afterEach(() => cleanup());

describe("DrawingsList 빈 목록", () => {
  it("도형이 없으면 개수만 0으로 보여주고 목록(ul)은 렌더하지 않는다", () => {
    render(<DrawingsList drawings={[]} onRemove={vi.fn()} />);

    expect(screen.getByText("그리기 (0)")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});

describe("DrawingsList 도형 라벨(drawingLabel 분기)", () => {
  const drawings: DrawingCollection = [
    { id: "d1", kind: "horizontal-line", price: 100 },
    { id: "d2", kind: "vertical-line", time: 42 },
    { id: "d3", kind: "trendline", points: [{ time: 1, price: 10 }, { time: 2, price: 20 }] },
    { id: "d4", kind: "rectangle", points: [{ time: 1, price: 10 }, { time: 2, price: 20 }] },
    {
      id: "d5",
      kind: "fibonacci",
      points: [{ time: 1, price: 10 }, { time: 2, price: 20 }],
      levels: [0, 1],
    },
  ];

  it("종류별로 서로 다른 라벨 문구를 렌더한다", () => {
    render(<DrawingsList drawings={drawings} onRemove={vi.fn()} />);

    expect(screen.getByText("그리기 (5)")).toBeInTheDocument();
    expect(screen.getByText("수평선 @100")).toBeInTheDocument();
    expect(screen.getByText("수직선 @42")).toBeInTheDocument();
    expect(screen.getByText("추세선 (1→2)")).toBeInTheDocument();
    expect(screen.getByText("사각형 (1→2)")).toBeInTheDocument();
    expect(screen.getByText("피보나치 (1→2)")).toBeInTheDocument();
  });

  it("삭제 버튼 클릭 시 해당 도형의 id로 onRemove를 호출한다", () => {
    const onRemove = vi.fn();
    render(<DrawingsList drawings={drawings} onRemove={onRemove} />);

    const removeButtons = screen.getAllByRole("button", { name: "삭제" });
    fireEvent.click(removeButtons[1]);

    expect(onRemove).toHaveBeenCalledWith("d2");
  });
});
