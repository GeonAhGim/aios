import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { createEmptyLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import { ChartLayoutErrorBanners } from "./ChartLayoutErrorBanners";
import type { UseChartLayoutResult } from "./useChartLayout";

afterEach(() => cleanup());

function baseLayout(overrides: Partial<UseChartLayoutResult> = {}): UseChartLayoutResult {
  return {
    status: "ready",
    restoreError: null,
    retryRestore: vi.fn(),
    model: createEmptyLayoutModel(),
    layoutId: null,
    layoutName: "기본 레이아웃",
    isDirty: false,
    saveStatus: "idle",
    saveError: null,
    save: vi.fn(async () => null),
    reload: vi.fn(async () => {}),
    rename: vi.fn(),
    remove: vi.fn(async () => {}),
    addPanel: vi.fn(),
    removePanel: vi.fn(),
    setActivePanel: vi.fn(),
    toggleWatchlistEntry: vi.fn(),
    objectTreeOrder: [],
    lockedIndicatorIds: [],
    setObjectTreeOrder: vi.fn(),
    setLockedIndicatorIds: vi.fn(),
    ...overrides,
  };
}

describe("ChartLayoutErrorBanners 정상 상태", () => {
  it("status=ready·saveStatus=idle이면 아무 배너도 렌더하지 않는다", () => {
    render(<ChartLayoutErrorBanners layout={baseLayout()} />);

    expect(screen.queryByText(/요청이 너무 많습니다|찾을 수 없습니다|충돌했습니다/)).not.toBeInTheDocument();
  });
});

describe("ChartLayoutErrorBanners 복원 실패(restore_failed)", () => {
  it("RATE_LIMIT_EXCEEDED면 배너 메시지와 재시도 버튼을 보여주고 클릭 시 retryRestore를 호출한다(DoD(a): backoff_retry 재시도 배선)", () => {
    const retryRestore = vi.fn();
    const restoreError = new ApiError(429, "과도한 요청", "trace-1", "RATE_LIMIT_EXCEEDED");
    render(
      <ChartLayoutErrorBanners
        layout={baseLayout({ status: "restore_failed", restoreError, retryRestore })}
      />,
    );

    expect(screen.getByText("지원코드: trace-1")).toBeInTheDocument();
    const retryButton = screen.getByRole("button", { name: "다시 시도" });
    expect(retryButton).toBeInTheDocument();

    fireEvent.click(retryButton);
    expect(retryRestore).toHaveBeenCalledTimes(1);
  });

  it("RESOURCE_NOT_FOUND(재시도 불가 코드)면 재시도 버튼을 렌더하지 않는다", () => {
    const restoreError = new ApiError(404, "찾을 수 없음", undefined, "RESOURCE_NOT_FOUND");
    render(<ChartLayoutErrorBanners layout={baseLayout({ status: "restore_failed", restoreError })} />);

    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });
});

describe("ChartLayoutErrorBanners 저장 실패(saveStatus=error)", () => {
  it("RATE_LIMIT_EXCEEDED면 재시도 버튼을 보여주고 클릭 시 layout.save를 호출한다(DoD(a): saveRouted 분기 재시도 배선)", () => {
    const save = vi.fn(async () => null);
    const saveError = new ApiError(429, "과도한 요청", "trace-2", "RATE_LIMIT_EXCEEDED");
    render(<ChartLayoutErrorBanners layout={baseLayout({ saveStatus: "error", saveError, save })} />);

    const retryButton = screen.getByRole("button", { name: "다시 시도" });
    fireEvent.click(retryButton);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("saveStatus가 idle로 돌아오면 저장 에러 배너가 사라진다", () => {
    const saveError = new ApiError(429, "과도한 요청", undefined, "RATE_LIMIT_EXCEEDED", 3);
    render(<ChartLayoutErrorBanners layout={baseLayout({ saveStatus: "idle", saveError })} />);

    expect(screen.queryByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).not.toBeInTheDocument();
  });
});
