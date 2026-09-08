import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ValidationResultView } from "@aios/shared-types";
import { ValidationRunPanel } from "./ValidationRunPanel";

const startValidationMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useStartValidation: () => ({ mutateAsync: startValidationMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  startValidationMutateAsync.mockReset();
});

const PASS_RESULT: ValidationResultView = {
  run_id: "run-1",
  strategy_id: "my-strategy",
  strategy_version: "1.0.0",
  check_type: "BACKTEST",
  state: "SUCCEEDED",
  outcome: "PASS",
  metrics: { sharpe: 1.2 },
  warnings: [],
  hard_fail_reasons: [],
  obligations: [],
  result_hash: "hash-1",
  created_at: "2026-09-08T00:00:00Z",
};

function renderPanel() {
  return render(
    <ValidationRunPanel
      strategyId="my-strategy"
      strategyVersion="1.0.0"
      exchange="bitget"
      symbol="BTC/USDT"
    />,
  );
}

// task-2412(FE-OPS-8): 검증 실행 버튼이 startValidation.mutateAsync를 strategyId/
// strategyVersion과 body로 호출하고, 성공 시 판정(outcome)·상태(state)·사유가
// DOM에 나타나는 것을 단언한다(DoD 성공 경로).
describe("ValidationRunPanel 성공 경로", () => {
  it("검증 실행 클릭 시 strategyId/strategyVersion과 body로 startValidation을 호출한다", async () => {
    startValidationMutateAsync.mockResolvedValue(PASS_RESULT);
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));

    await waitFor(() =>
      expect(startValidationMutateAsync).toHaveBeenCalledWith({
        strategyId: "my-strategy",
        strategyVersion: "1.0.0",
        body: {
          exchange: "bitget",
          symbol: "BTC/USDT",
          costModelFeeBps: 10,
          costModelSlippageBps: 5,
        },
      }),
    );
  });

  it("성공하면 판정(PASS)과 상태(SUCCEEDED)를 DOM에 보여준다", async () => {
    startValidationMutateAsync.mockResolvedValue(PASS_RESULT);
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));

    await waitFor(() => expect(screen.getByText("판정: 통과 (SUCCEEDED)")).toBeInTheDocument());
  });

  it("hard_fail_reasons·warnings가 있으면 이유 목록을 DOM에 보여준다", async () => {
    startValidationMutateAsync.mockResolvedValue({
      ...PASS_RESULT,
      outcome: "FAIL",
      hard_fail_reasons: ["max_drawdown_exceeded"],
      warnings: ["low_sample_size"],
    });
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));

    await waitFor(() => expect(screen.getByText("max_drawdown_exceeded")).toBeInTheDocument());
    expect(screen.getByText("low_sample_size")).toBeInTheDocument();
  });

  it("strategyId가 비어있으면 검증 실행 버튼이 비활성화된다", () => {
    render(
      <ValidationRunPanel strategyId="" strategyVersion="1.0.0" exchange="bitget" symbol="BTC/USDT" />,
    );
    expect(screen.getByRole("button", { name: "검증 실행" })).toBeDisabled();
  });
});

// spec §3.3 에러 taxonomy: 거래소 자격증명이 없는 사용자가 실행하면 오는 4xx는
// err.message를 직접 노출하지 않고 routeApiError로 판정해 ErrorMessage의 매핑
// 문구만 보여준다(task-1048 가드, task-901 패턴).
describe("ValidationRunPanel 실패 경로", () => {
  it("negative: 거래소 자격증명 없음(RESOURCE_NOT_FOUND, 404)은 err.message 대신 매핑 문구를 보여준다", async () => {
    startValidationMutateAsync.mockRejectedValue(
      new ApiError(404, "raw server detail", "trace-1", "RESOURCE_NOT_FOUND"),
    );
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));

    await waitFor(() =>
      expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    startValidationMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));

    await waitFor(() =>
      expect(screen.getByText("전략 검증 실행에 실패했습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
