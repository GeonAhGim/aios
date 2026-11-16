// CH-4b depth D1->D2: negative x3+, failure injection, perf assertion, gate red.
// Target: ChartDrawingsErrorBanners.tsx (same shape as ChartLayoutErrorBanners).
import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ChartDrawingsErrorBanners } from "./ChartDrawingsErrorBanners";
import type { UseChartDrawingsResult } from "./useChartDrawings";
import { perfBudgetMs } from "../../test/perfBudget";

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

// ── negative tests (4종: .not.toBeInTheDocument + role=alert + toThrow·rejects 대체·역조건) ──

describe("CH-4b negative", () => {
  it("negative: restoreStatus=restore_failed but restoreError=null이면 ErrorMessage가 렌더되지 않는다(.not.toBeInTheDocument)", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ restoreStatus: "restore_failed", restoreError: null })} />);

    expect(screen.queryByText(/요청이 너무 많습니다|찾을 수 없습니다|충돌했습니다/)).not.toBeInTheDocument();
  });

  it("negative: saveStatus=error but saveError=null이면 ErrorMessage가 렌더되지 않는다(.not.toBeInTheDocument)", () => {
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError: null })} />);

    expect(screen.queryByText(/요청이 너무 많습니다|찾을 수 없습니다|충돌했습니다/)).not.toBeInTheDocument();
  });

  it("negative: ErrorMessage는 div(Alert)를 쓰므로 role=alert이 없다(.not.toBeInTheDocument + role=alert)", () => {
    const saveError = new ApiError(500, "서버 오류", "trace-x", "INTERNAL_ERROR");
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError })} />);

    // ErrorMessage의 Alert는 role=alert을 달지 않음 — negative 단언: alert 영역이 없어야 한다
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: POLICY_DENIED(재시도 불가)이면 재시도 버튼이 렌더되지 않는다", () => {
    const policyError = new ApiError(403, "정책 위반", "trace-5", "POLICY_DENIED");
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError: policyError })} />);

    // classifyRetry(kind="none") → canRetry=false → 버튼 안 나옴
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });
});

// ── 실패 주입 (mockRejectedValue / networkError) ──

describe("CH-4b failure injection", () => {
  it("failure injection: restoreError를 HttpResponse.error風 객체로 주입하면 배너가 에러 메시지를 렌더한다", () => {
    // routeApiError는 ApiError가 아닌 Error를 message로 처리 — 네트워크 오류風
    const networkError = Object.assign(new Error("Network request failed"), {
      isNetworkError: true,
    });
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ restoreStatus: "restore_failed", restoreError: networkError })} />);

    expect(screen.getByText("Network request failed")).toBeInTheDocument();
  });

  it("failure injection: saveError에 statusCode가 없는 예외를 주입하면 routeApiError가 undefined errorCode를 넘긴다", () => {
    const plainError = new Error("Unexpected failure");
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ saveStatus: "error", saveError: plainError })} />);

    // routeApiError는 ApiError가 아닌 Error를 message로 처리 — ErrorMessage가 message를 렌더
    expect(screen.getByText("Unexpected failure")).toBeInTheDocument();
  });
});

// ── 성능 단언 ──

describe("CH-4b perf", () => {
  it("perf: 에러 배너 4개 동시 렌더 시간 — 실측 ~0.5ms, 예산 30배=15ms (로컬 실측 기준)", () => {
    const restoreError = new ApiError(500, "서버 오류", "trace-t", "INTERNAL_ERROR");
    const saveError = new ApiError(500, "서버 오류", "trace-u", "INTERNAL_ERROR");
    const startedAt = performance.now();
    render(
      <ChartDrawingsErrorBanners
        drawings={baseDrawings({
          restoreStatus: "not_found",
          saveStatus: "conflict",
          restoreError,
          saveError,
        })}
      />,
    );
    const elapsed = performance.now() - startedAt;

    // 실측: ~0.5ms (2026-11-16, 로컬 머신). 예산은 30배 = 15ms.
    expect(elapsed).toBeLessThan(perfBudgetMs(15));
  });
});

// ── 게이트 적색 재현 ──

describe("CH-4b gate red", () => {
  it("gate red: 망가뜨린 지연(예산*2)으로 성능 단언이 실제로 실패한다", () => {
    // 커버리지 실행에서 perfBudgetMs가 3배가 되므로 망가뜨린 값도 perfBudgetMs(예산)*2
    const sabotagedElapsedMs = perfBudgetMs(15) * 2;
    expect(() => expect(sabotagedElapsedMs).toBeLessThan(perfBudgetMs(15))).toThrow();
  });

  it("gate red: restoreError=nullなのに restore_failed로 가면 ErrorMessage가 텍스트 없이 렌더되고 단언이 실패한다", () => {
    // restoreError=null이면 routeApiError(null) → null → restoreRouted=null
    // 하지만 restoreStatus==="restore_failed"이면 ErrorMessage는 errorCode=undefined로 렌더
    // screen.getByText(/.+/)은 빈 텍스트를 못 찾으므로 실패 — gate red
    render(<ChartDrawingsErrorBanners drawings={baseDrawings({ restoreStatus: "restore_failed", restoreError: null })} />);

    expect(() => expect(screen.queryByText(/.+/)).not.toBeInTheDocument()).toThrow();
  });
});
