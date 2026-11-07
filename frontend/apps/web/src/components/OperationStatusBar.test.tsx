import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExecutionCardResponse } from "@aios/shared-types";
import { OperationStatusBar } from "./OperationStatusBar";

const pauseMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  usePauseExecution: () => ({ mutateAsync: pauseMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  pauseMutateAsync.mockReset();
});

function exec(overrides: Partial<ExecutionCardResponse>): ExecutionCardResponse {
  return {
    executionId: 1,
    strategyId: "s",
    strategyVersion: "1",
    status: "RUNNING",
    mode: "PAPER",
    exchange: "bitget",
    allocatedCapital: "100",
    daysSinceStart: 1,
    realizedPnl: "0",
    unrealizedPnl: "0",
    maxDrawdownPct: null,
    lastRiskVerdict: null,
    ...overrides,
  };
}

describe("OperationStatusBar — 상태 표시", () => {
  it("모의 실행만 있으면 '모의 운용', 실전 배정 자본 0, 실행 중 개수를 보여 준다", () => {
    render(
      <OperationStatusBar
        executions={[exec({ executionId: 1 }), exec({ executionId: 2, status: "PAUSED" })]}
        isLoading={false}
        isError={false}
      />,
    );

    expect(screen.getByText("모의 운용")).toBeInTheDocument();
    expect(screen.getByText("0.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("1개")).toBeInTheDocument();
  });

  it("실행 중인 실전 실행의 배정 자본만 합산한다(일시정지·종료된 실전은 제외)", () => {
    render(
      <OperationStatusBar
        executions={[
          exec({ executionId: 1, mode: "LIVE", allocatedCapital: "50" }),
          exec({ executionId: 2, mode: "LIVE", allocatedCapital: "30.5" }),
          exec({ executionId: 3, mode: "LIVE", allocatedCapital: "999", status: "PAUSED" }),
          exec({ executionId: 4, mode: "LIVE", allocatedCapital: "888", status: "RETIRED" }),
          exec({ executionId: 5, mode: "PAPER", allocatedCapital: "777" }),
        ]}
        isLoading={false}
        isError={false}
      />,
    );

    expect(screen.getByText("모의 + 실전")).toBeInTheDocument();
    expect(screen.getByText("80.50 USDT")).toBeInTheDocument();
    expect(screen.getByText("3개")).toBeInTheDocument();
  });

  it("실행이 하나도 없으면 '실행 없음'이고 모두 멈춤은 비활성이다", () => {
    render(<OperationStatusBar executions={[]} isLoading={false} isError={false} />);

    expect(screen.getByText("실행 없음")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "모두 멈춤" })).toBeDisabled();
  });

  it("[실패] 조회 중이거나 실패하면 0이 아니라 '알 수 없음'을 보여 주고 멈춤을 막는다", () => {
    const { rerender } = render(
      <OperationStatusBar executions={undefined} isLoading={true} isError={false} />,
    );
    expect(screen.getAllByText("알 수 없음")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "모두 멈춤" })).toBeDisabled();

    rerender(<OperationStatusBar executions={undefined} isLoading={false} isError={true} />);
    expect(screen.getAllByText("알 수 없음")).toHaveLength(3);
    expect(screen.getByText("운용 상태를 불러오지 못했습니다.")).toBeInTheDocument();
    expect(screen.queryByText("0.00 USDT")).not.toBeInTheDocument();
  });
});

describe("OperationStatusBar — 모두 멈춤", () => {
  it("한 번 누르면 실행 중인 항목 전부를 일시정지하고 결과를 알린다", async () => {
    pauseMutateAsync.mockResolvedValue({});
    render(
      <OperationStatusBar
        executions={[
          exec({ executionId: 1 }),
          exec({ executionId: 2, mode: "LIVE" }),
          exec({ executionId: 3, status: "PAUSED" }),
        ]}
        isLoading={false}
        isError={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "모두 멈춤" }));

    await waitFor(() => expect(screen.getByText("2개를 일시정지했습니다.")).toBeInTheDocument());
    expect(pauseMutateAsync.mock.calls.map((c) => c[0]).sort()).toEqual([1, 2]);
  });

  it("[실패 주입] 일부가 실패하면 성공으로 보고하지 않고 실패 개수를 알린다", async () => {
    pauseMutateAsync.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("boom"));
    render(
      <OperationStatusBar
        executions={[exec({ executionId: 1 }), exec({ executionId: 2 })]}
        isLoading={false}
        isError={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "모두 멈춤" }));

    await waitFor(() =>
      expect(screen.getByText(/2개 중 1개를 멈추지 못했습니다/)).toBeInTheDocument(),
    );
  });
});
