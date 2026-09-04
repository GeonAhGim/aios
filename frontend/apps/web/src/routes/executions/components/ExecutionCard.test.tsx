import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ExecutionCardResponse } from "@aios/shared-types";
import { ExecutionCard } from "./ExecutionCard";

const startMutateAsync = vi.fn();
const pauseMutate = vi.fn();
const retireMutate = vi.fn();
const setRiskGuardMutate = vi.fn();
let startIsError = false;
let startError: unknown = null;

vi.mock("@aios/shared-hooks", () => ({
  useStartExecution: () => ({
    mutateAsync: startMutateAsync,
    isPending: false,
    isError: startIsError,
    error: startError,
  }),
  usePauseExecution: () => ({ mutate: pauseMutate, isPending: false }),
  useRetireExecution: () => ({ mutate: retireMutate, isPending: false }),
  useSetExecutionRiskGuard: () => ({ mutate: setRiskGuardMutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  startMutateAsync.mockReset();
  pauseMutate.mockReset();
  retireMutate.mockReset();
  setRiskGuardMutate.mockReset();
  startIsError = false;
  startError = null;
});

function execution(overrides: Partial<ExecutionCardResponse> = {}): ExecutionCardResponse {
  return {
    executionId: 1,
    strategyId: "strat-1",
    strategyVersion: "1",
    status: "PENDING",
    mode: "PAPER",
    exchange: "bitget",
    allocatedCapital: "1000.00",
    daysSinceStart: null,
    realizedPnl: "10.00",
    unrealizedPnl: "-5.00",
    maxDrawdownPct: null,
    ...overrides,
  };
}

describe("ExecutionCard 기본 표시", () => {
  it("전략 ID·거래소 라벨·모드·배분·손익·상태를 보여준다", () => {
    render(<ExecutionCard execution={execution()} />);

    expect(screen.getByText("strat-1")).toBeInTheDocument();
    expect(screen.getByText(/Bitget/)).toBeInTheDocument();
    expect(screen.getByText(/PAPER/)).toBeInTheDocument();
    expect(screen.getByText(/1000\.00/)).toBeInTheDocument();
    expect(screen.getByText("실현 손익 10.00")).toBeInTheDocument();
    expect(screen.getByText("미실현 손익 -5.00")).toBeInTheDocument();
    expect(screen.getByText("PENDING")).toBeInTheDocument();
  });

  it("RUNNING이 아니고 RETIRED가 아니면 시작 버튼만 보여준다", () => {
    render(<ExecutionCard execution={execution({ status: "PENDING" })} />);

    expect(screen.getByRole("button", { name: "시작" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "일시정지" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "중지" })).toBeInTheDocument();
  });

  it("RUNNING이면 일시정지 버튼을 보여주고 시작 버튼은 숨긴다", () => {
    render(<ExecutionCard execution={execution({ status: "RUNNING" })} />);

    expect(screen.getByRole("button", { name: "일시정지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "시작" })).not.toBeInTheDocument();
  });

  it("RETIRED면 위험 관리 입력과 시작/일시정지/중지 버튼을 모두 숨긴다", () => {
    render(<ExecutionCard execution={execution({ status: "RETIRED" })} />);

    expect(screen.queryByPlaceholderText("비활성")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "시작" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "일시정지" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "중지" })).not.toBeInTheDocument();
  });
});

describe("ExecutionCard 액션 배선", () => {
  it("시작 버튼 클릭 시 useIdempotentSubmit이 만든 idempotencyKey와 함께 start.mutateAsync를 호출한다", async () => {
    startMutateAsync.mockResolvedValue({});
    render(<ExecutionCard execution={execution({ status: "PENDING", executionId: 42 })} />);

    fireEvent.click(screen.getByRole("button", { name: "시작" }));

    await waitFor(() => expect(startMutateAsync).toHaveBeenCalledTimes(1));
    const call = startMutateAsync.mock.calls[0][0] as { executionId: number; idempotencyKey: string };
    expect(call.executionId).toBe(42);
    expect(typeof call.idempotencyKey).toBe("string");
    expect(call.idempotencyKey.length).toBeGreaterThan(0);
  });

  it("일시정지 버튼 클릭 시 pause.mutate(executionId)를 호출한다", () => {
    render(<ExecutionCard execution={execution({ status: "RUNNING", executionId: 7 })} />);

    fireEvent.click(screen.getByRole("button", { name: "일시정지" }));

    expect(pauseMutate).toHaveBeenCalledWith(7);
  });

  it("중지 버튼 클릭 시 retire.mutate({ executionId })를 호출한다", () => {
    render(<ExecutionCard execution={execution({ status: "PENDING", executionId: 9 })} />);

    fireEvent.click(screen.getByRole("button", { name: "중지" }));

    expect(retireMutate).toHaveBeenCalledWith({ executionId: 9 });
  });

  it("적용 버튼 클릭 시 입력한 손실 한도(%)로 setRiskGuard.mutate를 호출한다", () => {
    render(<ExecutionCard execution={execution({ status: "PENDING", executionId: 3 })} />);

    fireEvent.change(screen.getByPlaceholderText("비활성"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "적용" }));

    expect(setRiskGuardMutate).toHaveBeenCalledWith({
      executionId: 3,
      body: { maxDrawdownPct: "20" },
    });
  });

  it("손실 한도 입력을 비우면 maxDrawdownPct를 null로 보낸다(비활성)", () => {
    render(
      <ExecutionCard
        execution={execution({ status: "PENDING", executionId: 3, maxDrawdownPct: "15" })}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("비활성"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "적용" }));

    expect(setRiskGuardMutate).toHaveBeenCalledWith({
      executionId: 3,
      body: { maxDrawdownPct: null },
    });
  });
});

// spec §3.3 에러 taxonomy: 재시작 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage 경로로만
// 보여준다(task-901/task-1048 패턴).
describe("ExecutionCard 시작 실패 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", () => {
    startIsError = true;
    startError = new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED");
    render(<ExecutionCard execution={execution({ status: "PENDING" })} />);

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 Error.message를 그대로 보여준다(다른 화면과 동일한 ErrorMessage 폴백 규칙)", () => {
    startIsError = true;
    startError = new Error("ECONNRESET");
    render(<ExecutionCard execution={execution({ status: "PENDING" })} />);

    expect(screen.getByText("ECONNRESET")).toBeInTheDocument();
  });
});
