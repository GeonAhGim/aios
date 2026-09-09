import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ChartDrawingsErrorBanners } from "./ChartDrawingsErrorBanners";
import type { UseChartDrawingsResult } from "./useChartDrawings";

afterEach(() => cleanup());

function baseDrawings(overrides: Partial<UseChartDrawingsResult> = {}): UseChartDrawingsResult {
  return {
    drawingTool: null,
    setDrawingTool: vi.fn(),
    drawings: [],
    handleAddDrawing: vi.fn(),
    handleRemoveDrawing: vi.fn(),
    restoreStatus: "ready",
    restoreError: null,
    retryRestore: vi.fn(),
    saveStatus: "idle",
    saveError: null,
    persist: vi.fn(async () => {}),
    retryPersist: vi.fn(),
    ...overrides,
  };
}

describe("ChartDrawingsErrorBanners 정상 상태", () => {
  it("restoreStatus=ready·saveStatus=idle이면 아무 배너도 렌더하지 않는다", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings()} />);

    expect(screen.queryByText(/요청이 너무 많습니다|찾을 수 없습니다|충돌했습니다/)).not.toBeInTheDocument();
  });
});

describe("ChartDrawingsErrorBanners 복원 실패(restoreStatus)", () => {
  it("not_found면 고정 RESOURCE_NOT_FOUND 배너를 재시도 버튼 없이 보여준다(routeApiError를 거치지 않는 분류된 결과)", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ restoreStatus: "not_found" })} />);

    expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("restore_failed·RATE_LIMIT_EXCEEDED면 재시도 버튼을 보여주고 클릭 시 retryRestore를 호출한다(DoD(a): backoff_retry 재시도 배선)", () => {
    const retryRestore = vi.fn();
    const restoreError = new ApiError(429, "과도한 요청", "trace-1", "RATE_LIMIT_EXCEEDED");
    render(
      <ChartDrawingsErrorBanners
        drawings={baseDrawings({ restoreStatus: "restore_failed", restoreError, retryRestore })}
      />,
    );

    const retryButton = screen.getByRole("button", { name: "다시 시도" });
    fireEvent.click(retryButton);
    expect(retryRestore).toHaveBeenCalledTimes(1);
  });
});

describe("ChartDrawingsErrorBanners 저장 실패(saveStatus)", () => {
  it("conflict면 고정 STATE_CONCURRENCY_CONFLICT 배너를 재시도 버튼 없이 보여준다", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "conflict" })} />);

    expect(screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("not_found면 고정 RESOURCE_NOT_FOUND 배너를 보여준다", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "not_found" })} />);

    expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
  });

  it("error·RATE_LIMIT_EXCEEDED면 재시도 버튼을 보여주고 클릭 시 retryPersist를 호출한다(DoD(a)/(b): backoff_retry 저장 재시도 배선, 형제 ChartLayoutErrorBanners와 동일 형태)", () => {
    const retryPersist = vi.fn();
    const saveError = new ApiError(429, "과도한 요청", "trace-2", "RATE_LIMIT_EXCEEDED");
    render(
      <ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError, retryPersist })} />,
    );

    expect(screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument();
    expect(screen.getByText("지원코드: trace-2")).toBeInTheDocument();
    const retryButton = screen.getByRole("button", { name: "다시 시도" });
    fireEvent.click(retryButton);
    expect(retryPersist).toHaveBeenCalledTimes(1);
  });

  it("error·POLICY_DENIED(재시도 불가)면 배너 메시지는 렌더되지만 재시도 버튼은 렌더되지 않는다(DoD(b): 재시도 불가 분류는 배선하지 않는다)", () => {
    const retryPersist = vi.fn();
    const saveError = new ApiError(403, "정책 위반", "trace-3", "POLICY_DENIED");
    render(
      <ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError, retryPersist })} />,
    );

    expect(screen.getByText("정책에 의해 거부된 요청입니다. 세부 사유를 확인해주세요.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(retryPersist).not.toHaveBeenCalled();
  });

  it("saveStatus가 idle로 돌아오면 저장 에러 배너가 사라진다", () => {
    const saveError = new ApiError(429, "과도한 요청", undefined, "RATE_LIMIT_EXCEEDED", 3);
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "idle", saveError })} />);

    expect(screen.queryByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).not.toBeInTheDocument();
  });
});
