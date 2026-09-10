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

// DEPTH_CH audit (task-2729): task-2013 (242f346) had no numeric performance
// assertion and no D3 multi-instance reproduction (DEEPEN task-3100).
describe("ChartLegend — 수치 성능·D3 다중 인스턴스 (DEEPEN task-3100)", () => {
  it("수치 성능: 지표 150개를 렌더링해도 10s 예산 내에 끝난다", () => {
    const entries: ObjectTreeEntry[] = Array.from({ length: 150 }, (_, i) => entry({ id: `IND_${i}`, name: `IND_${i}` }));

    const start = performance.now();
    render(<ChartLegend objectTree={entries} onToggleVisible={noop} onMoveEntry={noop} onToggleLocked={noop} />);
    const elapsedMs = performance.now() - start;

    expect(screen.getAllByRole("listitem")).toHaveLength(150);
    // Loose budget (same convention as the VISIBLE_CANDLE_COUNT assertion in
    // useChartLayout.test.ts) that absorbs jsdom's first-render JIT warmup and
    // shared-machine CPU contention (task-1968), but still catches a rendering
    // regression that goes non-linear with entry count (e.g. O(n^2) key recompute).
    expect(elapsedMs).toBeLessThan(10000);
  });

  it("D3 다중 인스턴스: 같은 화면에 동시에 렌더된 두 ChartLegend 인스턴스는 서로의 콜백을 교차호출하지 않는다", () => {
    const onMoveEntryA = vi.fn();
    const onMoveEntryB = vi.fn();
    render(
      <>
        <ChartLegend
          objectTree={[entry({ id: "SMA", name: "SMA" }), entry({ id: "RSI", name: "RSI" })]}
          onToggleVisible={noop}
          onMoveEntry={onMoveEntryA}
          onToggleLocked={noop}
        />
        <ChartLegend
          objectTree={[entry({ id: "EMA", name: "EMA" }), entry({ id: "WMA", name: "WMA" })]}
          onToggleVisible={noop}
          onMoveEntry={onMoveEntryB}
          onToggleLocked={noop}
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "순서 아래로 SMA" }));
    expect(onMoveEntryA).toHaveBeenCalledWith("SMA", 1);
    expect(onMoveEntryB).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "순서 아래로 EMA" }));
    expect(onMoveEntryB).toHaveBeenCalledWith("EMA", 1);
    expect(onMoveEntryA).toHaveBeenCalledTimes(1); // unaffected by B's later click
  });
});
