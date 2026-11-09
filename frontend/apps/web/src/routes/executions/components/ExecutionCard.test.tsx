import "../../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ExecutionCardResponse } from "@aios/shared-types";
import { ExecutionCard } from "./ExecutionCard";

const expandMutateAsync = vi.fn();
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
  useLiveReadiness: () => ({ isLoading: true, isError: false, data: undefined }),
  useLiveConversion: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useExpandExposure: () => ({ mutateAsync: expandMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  expandMutateAsync.mockReset();
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
    lastRiskVerdict: null,
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

  it("lastRiskVerdict가 없으면 판정 패널을 보여주지 않는다", () => {
    render(<ExecutionCard execution={execution({ lastRiskVerdict: null })} />);

    expect(screen.queryByText("리스크/컴플라이언스 판정")).not.toBeInTheDocument();
  });

  it("lastRiskVerdict.outcome이 ALLOW면 판정 패널을 생략한다", () => {
    render(
      <ExecutionCard
        execution={execution({
          lastRiskVerdict: { outcome: "ALLOW", reasonCodes: [], evaluatedAt: "2026-01-01T00:00:00Z" },
        })}
      />,
    );

    expect(screen.queryByText("리스크/컴플라이언스 판정: 승인")).not.toBeInTheDocument();
  });

  it("lastRiskVerdict.outcome이 DENY면 사유 코드와 함께 danger 톤 판정 패널을 보여준다", () => {
    render(
      <ExecutionCard
        execution={execution({
          lastRiskVerdict: {
            outcome: "DENY",
            reasonCodes: ["RSK-007"],
            evaluatedAt: "2026-01-01T00:00:00Z",
          },
        })}
      />,
    );

    expect(screen.getByText("리스크/컴플라이언스 판정: 거부")).toBeInTheDocument();
    expect(screen.getByText("사유: RSK-007")).toBeInTheDocument();
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

describe("ExecutionCard 실전 전환 진입 (J8 LP-5)", () => {
  it("모의 실행에는 '실전 전환' 버튼이 있고 누르면 전환 대화상자가 열린다", () => {
    render(<ExecutionCard execution={execution({ mode: "PAPER" })} />);

    fireEvent.click(screen.getByRole("button", { name: "실전 전환" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/실전 주문은 안전 장치로 차단되어 있습니다/)).toBeInTheDocument();
  });

  it("[부정] 이미 실전인 실행이나 종료된 실행에는 '실전 전환' 버튼이 없다", () => {
    const { rerender } = render(<ExecutionCard execution={execution({ mode: "LIVE" })} />);
    expect(screen.queryByRole("button", { name: "실전 전환" })).not.toBeInTheDocument();

    rerender(<ExecutionCard execution={execution({ mode: "PAPER", status: "RETIRED" })} />);
    expect(screen.queryByRole("button", { name: "실전 전환" })).not.toBeInTheDocument();
  });

  it("대화상자의 취소로 닫힌다", () => {
    render(<ExecutionCard execution={execution({ mode: "PAPER" })} />);
    fireEvent.click(screen.getByRole("button", { name: "실전 전환" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ExecutionCard 노출 확대 진입 (J8 LP-4, W4c)", () => {
  it("LIVE 실행에는 '노출 확대' 버튼이 있고 누르면 대화상자가 열린다", () => {
    render(<ExecutionCard execution={execution({ mode: "LIVE", status: "RUNNING" })} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "노출 확대" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("[부정] PAPER 실행에는 '노출 확대' 버튼이 없다", () => {
    render(<ExecutionCard execution={execution({ mode: "PAPER" })} />);

    expect(screen.queryByRole("button", { name: "노출 확대" })).not.toBeInTheDocument();
  });

  it("[부정] RETIRED 실행에는 '노출 확대' 버튼이 없다", () => {
    render(
      <ExecutionCard execution={execution({ mode: "LIVE", status: "RETIRED" })} />,
    );

    expect(screen.queryByRole("button", { name: "노출 확대" })).not.toBeInTheDocument();
  });

  it.each([0, 1])("[부정] 취소 버튼 %i로 확대 요청 없이 대화상자가 닫힌다", (index) => {
    render(<ExecutionCard execution={execution({ mode: "LIVE", status: "RUNNING" })} />);

    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "취소" })[index]);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(expandMutateAsync).not.toHaveBeenCalled();
  });
});
