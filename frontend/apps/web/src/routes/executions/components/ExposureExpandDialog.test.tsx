import "../../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ExecutionCardResponse } from "@aios/shared-types";
import { ExposureExpandDialog } from "./ExposureExpandDialog";

const expandMutateAsync = vi.fn();
let expandState = { isLoading: false, isPending: false, isError: false };

vi.mock("@aios/shared-hooks", () => ({
  useExpandExposure: () => ({
    mutateAsync: expandMutateAsync,
    isPending: expandState.isPending,
  }),
}));

afterEach(() => {
  cleanup();
  expandMutateAsync.mockReset();
  expandState = { isLoading: false, isPending: false, isError: false };
});

function execution(overrides: Partial<ExecutionCardResponse> = {}): ExecutionCardResponse {
  return {
    executionId: 42,
    strategyId: "strat-42",
    strategyVersion: "1",
    status: "RUNNING",
    mode: "LIVE",
    exchange: "bitget",
    allocatedCapital: "1000",
    daysSinceStart: 10,
    realizedPnl: "50",
    unrealizedPnl: "10",
    maxDrawdownPct: null,
    lastRiskVerdict: null,
    ...overrides,
  };
}

describe("ExposureExpandDialog — 노출 범위 검증", () => {
  it("[부정] 노출이 1 미만이면 요청을 보내지 않는다", async () => {
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(expandMutateAsync).not.toHaveBeenCalled());
    expect(screen.getByText("새 노출은 1 이상 100 이하여야 합니다.")).toBeInTheDocument();
  });

  it("[부정] 노출이 100 초과이면 요청을 보내지 않는다", async () => {
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "101" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(expandMutateAsync).not.toHaveBeenCalled());
    expect(screen.getByText("새 노출은 1 이상 100 이하여야 합니다.")).toBeInTheDocument();
  });

  it("[부정] 빈 입력이면 요청을 보내지 않는다", async () => {
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(expandMutateAsync).not.toHaveBeenCalled());
  });
});

describe("ExposureExpandDialog — 성공 호출 body 정확성", () => {
  it("정수 노출, 비밀번호만 입력 시 totpCode는 null", async () => {
    expandMutateAsync.mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "mypassword" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(expandMutateAsync).toHaveBeenCalledOnce());
    const arg = expandMutateAsync.mock.calls[0][0];
    expect(arg.executionId).toBe(42);
    expect(arg.body).toEqual({
      exposurePct: "50",
      currency: "USDT",
      exchange: "bitget",
      password: "mypassword",
      totpCode: null,
    });
  });

  it("TOTP 코드가 있으면 totpCode에 포함한다", async () => {
    expandMutateAsync.mockResolvedValue(undefined);
    render(<ExposureExpandDialog execution={execution()} onClose={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "75" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw" } });
    fireEvent.change(screen.getByLabelText("인증 코드 (MFA를 켠 경우)"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(expandMutateAsync).toHaveBeenCalledOnce());
    const arg = expandMutateAsync.mock.calls[0][0];
    expect(arg.body.totpCode).toBe("123456");
  });
});

describe("ExposureExpandDialog — 서버 거부 시 동작", () => {
  it("[실패 주입] 서버가 401을 반환하면 비밀번호·TOTP를 비우고 닫지 않는다", async () => {
    expandMutateAsync.mockRejectedValue(
      new ApiError(401, "비밀번호가 일치하지 않습니다.", "trace-abc", "AUTH_INVALID_CREDENTIALS"),
    );
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "60" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "wrongpw" } });
    fireEvent.change(screen.getByLabelText("인증 코드 (MFA를 켠 경우)"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(screen.getByLabelText("비밀번호")).toHaveValue(""));
    await waitFor(() => expect(screen.getByLabelText("인증 코드 (MFA를 켠 경우)")).toHaveValue(""));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "노출 확대" })).toBeInTheDocument();
  });

  it("[실패 주입] 서버가 403을 반환하면 ForbiddenNotice를 표시하고 닫히지 않는다", async () => {
    expandMutateAsync.mockRejectedValue(
      new ApiError(403, "인가되지 않은 요청입니다.", "trace-xyz", "AUTH_FORBIDDEN"),
    );
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "80" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "badpw" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(onClose).not.toHaveBeenCalled());
  });
});

describe("ExposureExpandDialog — 성공 시 상태 정리", () => {
  it("성공하면 비밀번호를 비우고 onClose를 호출한다", async () => {
    expandMutateAsync.mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    fireEvent.change(screen.getByLabelText("새 노출 (%)"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "goodpw" } });
    fireEvent.click(screen.getByRole("button", { name: "노출 확대" }));

    await waitFor(() => expect(screen.getByLabelText("비밀번호")).toHaveValue(""));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });
});

describe("ExposureExpandDialog — 취소", () => {
  it("취소 버튼을 누르면 onClose만 호출하고 요청을 보내지 않는다", async () => {
    const onClose = vi.fn();
    render(<ExposureExpandDialog execution={execution()} onClose={onClose} />);

    const cancelButtons = screen.getAllByRole("button", { name: "취소" });
    // form 안의 취소 버튼 (type="button")을 클릭
    fireEvent.click(cancelButtons[1]);

    expect(onClose).toHaveBeenCalledOnce();
    expect(expandMutateAsync).not.toHaveBeenCalled();
  });
});

describe("ExposureExpandDialog — guardNotice 표시", () => {
  it("guardNotice를 경고 형태로 표시한다", () => {
    render(<ExposureExpandDialog execution={execution()} onClose={vi.fn()} />);

    expect(
      screen.getByText("현재 실전 주문은 안전 장치로 차단되어 있습니다. 확대해도 별도 승인 전까지 실제 주문은 나가지 않습니다."),
    ).toBeInTheDocument();
  });
});

describe("ExposureExpandDialog — 현재 허용 자본 표시", () => {
  it("execution.allocatedCapital을 현재 허용 자본으로 보여준다", () => {
    render(<ExposureExpandDialog execution={execution()} onClose={vi.fn()} />);

    expect(screen.getByText("현재 허용 자본")).toBeInTheDocument();
    expect(screen.getByText("1000 USDT")).toBeInTheDocument();
  });
});

describe("ExposureExpandDialog — onlyIncrease 안내", () => {
  it("onlyIncrease 안내 문구를 표시한다", () => {
    render(<ExposureExpandDialog execution={execution()} onClose={vi.fn()} />);

    expect(
      screen.getByText("노출은 늘리는 방향으로만 바꿀 수 있습니다. 줄이려면 실행을 일시정지하세요."),
    ).toBeInTheDocument();
  });
});
