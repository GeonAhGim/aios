import "../../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ExecutionCardResponse, LiveReadinessResponse } from "@aios/shared-types";
import { LiveConversionDialog } from "./LiveConversionDialog";

const convertMutateAsync = vi.fn();
let readinessState: { isLoading: boolean; isError: boolean; data: LiveReadinessResponse | undefined } =
  { isLoading: false, isError: false, data: undefined };

vi.mock("@aios/shared-hooks", () => ({
  useLiveReadiness: () => readinessState,
  useLiveConversion: () => ({ mutateAsync: convertMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  convertMutateAsync.mockReset();
  readinessState = { isLoading: false, isError: false, data: undefined };
});

function execution(overrides: Partial<ExecutionCardResponse> = {}): ExecutionCardResponse {
  return {
    executionId: 7,
    strategyId: "strat-7",
    strategyVersion: "1",
    status: "RUNNING",
    mode: "PAPER",
    exchange: "bitget",
    allocatedCapital: "500",
    daysSinceStart: 3,
    realizedPnl: "0",
    unrealizedPnl: "0",
    maxDrawdownPct: null,
    lastRiskVerdict: null,
    ...overrides,
  };
}

function readiness(overrides: Partial<LiveReadinessResponse> = {}): LiveReadinessResponse {
  return {
    orderCount: 12,
    terminalCount: 10,
    openCount: 2,
    unknownCount: 0,
    filledCount: 8,
    partialFillCount: 1,
    unfilledCount: 1,
    rejectedCount: 1,
    fillRatio: "0.8",
    partialFillRatio: "0.0833",
    unfilledRatio: "0.1",
    rejectRatio: "0.1",
    periodStart: "2026-11-01T00:00:00Z",
    periodEnd: "2026-11-04T00:00:00Z",
    periodDays: "3",
    avgSlippageBps: "4.2",
    slippageSampleSize: 8,
    feeTotals: null,
    netPnl: null,
    recoveryEventCount: null,
    unavailable: {
      fee_totals: "수수료 기록이 없습니다",
      net_pnl: "원천 데이터 없음",
      recovery_event_count: "원천 데이터 없음",
    },
    ...overrides,
  };
}

function fillInputs({ maxLoss = "50", pct = "10" }: { maxLoss?: string; pct?: string } = {}) {
  fireEvent.change(screen.getByLabelText("손실 한도 (USDT)"), { target: { value: maxLoss } });
  fireEvent.change(screen.getByLabelText("초기 노출 (%)"), { target: { value: pct } });
}

describe("LiveConversionDialog — 근거 표시", () => {
  it("실전 주문이 차단되어 있다는 사실과 실자금 사용을 알린다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    expect(screen.getByText(/실전 주문은 안전 장치로 차단되어 있습니다/)).toBeInTheDocument();
    expect(screen.getByText(/실제 자금을 사용합니다/)).toBeInTheDocument();
  });

  it("값이 없는 항목은 0이 아니라 '알 수 없음'과 서버가 준 사유로 보여 준다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    expect(screen.getByText("알 수 없음 (수수료 기록이 없습니다)")).toBeInTheDocument();
    expect(screen.getAllByText("알 수 없음 (원천 데이터 없음)")).toHaveLength(2);
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText("4.2 bp (표본 8건)")).toBeInTheDocument();
  });

  it("[실패] 근거 조회가 실패하면 근거 없이 전환하지 말라는 경고를 보여 준다", () => {
    readinessState = { isLoading: false, isError: true, data: undefined };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    expect(screen.getByText(/실적을 확인하지 못한 채 전환하지 마세요/)).toBeInTheDocument();
  });
});

describe("LiveConversionDialog — 입력 검증과 2단계 확인", () => {
  it("[부정] 손실 한도가 목표 자본보다 크면 확인 단계로 넘어가지 않는다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    fillInputs({ maxLoss: "501" });
    fireEvent.click(screen.getByRole("button", { name: "확인 단계로" }));

    expect(screen.getByText("손실 한도는 0보다 크고 목표 자본 이하여야 합니다.")).toBeInTheDocument();
    expect(screen.queryByLabelText("비밀번호")).not.toBeInTheDocument();
  });

  it("[부정] 초기 노출이 범위를 벗어나면 확인 단계로 넘어가지 않는다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    fillInputs({ pct: "0" });
    fireEvent.click(screen.getByRole("button", { name: "확인 단계로" }));

    expect(screen.getByText("초기 노출은 1 이상 100 이하여야 합니다.")).toBeInTheDocument();
  });

  it("처음 열리는 자본을 목표 자본 × 초기 노출로 미리 보여 준다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    fillInputs({ pct: "10" });

    expect(screen.getByText("약 50.00 USDT")).toBeInTheDocument();
  });

  it("확인 단계에서 비밀번호와 함께 전환을 요청하고 성공하면 닫는다", async () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    convertMutateAsync.mockResolvedValue({ id: 99 });
    const onClose = vi.fn();
    render(<LiveConversionDialog execution={execution()} onClose={onClose} />);

    fillInputs({ maxLoss: "25", pct: "10" });
    fireEvent.click(screen.getByRole("button", { name: "확인 단계로" }));
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw-1" } });
    fireEvent.click(screen.getByRole("button", { name: "실전으로 전환" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(convertMutateAsync).toHaveBeenCalledTimes(1);
    const arg = convertMutateAsync.mock.calls[0][0];
    expect(arg.executionId).toBe(7);
    expect(arg.idempotencyKey).toBeTruthy();
    expect(arg.body).toEqual({
      allocatedCapital: "500",
      currency: "USDT",
      exchange: "bitget",
      maxLossAmount: "25",
      initialExposurePct: "10",
      password: "pw-1",
      totpCode: null,
    });
  });

  it("[실패 주입] 서버가 거부하면 오류를 보여 주고 비밀번호를 비우며 닫지 않는다", async () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    convertMutateAsync.mockRejectedValue(
      new ApiError(403, "재인증에 실패했습니다.", "trace-1", "AUTH_INVALID_CREDENTIALS"),
    );
    const onClose = vi.fn();
    render(<LiveConversionDialog execution={execution()} onClose={onClose} />);

    fillInputs({ maxLoss: "25" });
    fireEvent.click(screen.getByRole("button", { name: "확인 단계로" }));
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "실전으로 전환" }));

    await waitFor(() => expect(screen.getByLabelText("비밀번호")).toHaveValue(""));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "실전으로 전환" })).toBeInTheDocument();
  });

  it("이전 버튼으로 입력 단계로 돌아간다", () => {
    readinessState = { isLoading: false, isError: false, data: readiness() };
    render(<LiveConversionDialog execution={execution()} onClose={vi.fn()} />);

    fillInputs({ maxLoss: "25" });
    fireEvent.click(screen.getByRole("button", { name: "확인 단계로" }));
    fireEvent.click(screen.getByRole("button", { name: "이전" }));

    expect(screen.getByLabelText("손실 한도 (USDT)")).toHaveValue(25);
  });
});
