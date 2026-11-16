import "../i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MfaStepUpDialog } from "./MfaStepUpDialog";
import { perfBudgetMs } from "../test/perfBudget";

let registeredHandler: (() => Promise<boolean>) | null = null;
const configureMfaStepUpHandlerMock = vi.fn((handler: (() => Promise<boolean>) | null) => {
  registeredHandler = handler;
});

vi.mock("@aios/api-client", () => ({
  configureMfaStepUpHandler: (handler: (() => Promise<boolean>) | null) => configureMfaStepUpHandlerMock(handler),
}));

const mutateAsyncMock = vi.fn();
const isPendingMock = vi.fn(() => false);
vi.mock("@aios/shared-hooks", () => ({
  useVerifyMfa: () => ({ mutateAsync: mutateAsyncMock, isPending: isPendingMock() }),
}));

function openDialog(): Promise<boolean> {
  let promise!: Promise<boolean>;
  act(() => {
    promise = registeredHandler!();
  });
  return promise;
}

// 실측 기준: MfaStepUpDialog 렌더 + TOTP 제출 → ~2ms (로컬 2026-11-16)
// 예산 = 실측 30배 = 60ms. perfBudgetMs()가 coverage 모드에서 3배 확대.
const RENDER_BUDGET_MS = 60;

describe("MfaStepUpDialog depth1", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    registeredHandler = null;
  });

  // ── negative 3종 ──────────────────────────────────────────────

  it("[negative] 빈 코드 제출 시 form required 검증으로 handleSubmit이 호출되지 않는다", async () => {
    render(<MfaStepUpDialog />);

    const resultPromise = openDialog();
    // 빈 입력으로 제출 — required 검증으로 handleSubmit이 호출되지 않음
    fireEvent.click(screen.getByRole("button", { name: "확인" }));

    // mutateAsync가 호출되지 않음
    expect(mutateAsyncMock).not.toHaveBeenCalled();
    // 다이얼로그는 여전히 열려 있음
    expect(screen.getByText("추가 인증이 필요합니다")).toBeInTheDocument();
    // 입력값은 여전히 비어 있음
    const input = screen.getByPlaceholderText("6자리 코드") as HTMLInputElement;
    expect(input.value).toBe("");
    // 취소해서 promise를 resolve한다
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    await expect(resultPromise).resolves.toBe(false);
  });

  it("[negative] 잘못된 TOTP로 실패 시 원요청이 재시도되지 않고 다이얼로그가 닫히지 않는다", async () => {
    mutateAsyncMock.mockRejectedValue(new Error("AUTH_MFA_INVALID"));
    render(<MfaStepUpDialog />);

    let resolved: boolean | undefined;
    const resultPromise = openDialog().then((ok) => {
      resolved = ok;
      return ok;
    });
    void resultPromise;

    fireEvent.change(screen.getByPlaceholderText("6자리 코드"), { target: { value: "badcode" } });
    fireEvent.click(screen.getByRole("button", { name: "확인" }));

    // 실패 후에도 다이얼로그가 열려 있음
    expect(screen.getByText("추가 인증이 필요합니다")).toBeInTheDocument();
    // 아직 resolve되지 않음
    expect(resolved).toBeUndefined();
    // 에러 메시지 표시됨
    await waitFor(() => expect(screen.getByText(/인증에 실패했습니다/)).toBeInTheDocument());
    // 취소해서 promise를 resolve한다
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    await expect(resultPromise).resolves.toBe(false);
  });

  it("[negative] 다이얼로그가 열리기 전에는 registeredHandler가 null이다", () => {
    const { unmount } = render(<MfaStepUpDialog />);
    unmount();
    expect(registeredHandler).toBeNull();
  });

  // ── 실패 주입 ────────────────────────────────────────────────

  it("[failure injection] mutateAsync가 TypeError(Network error)를 던지면 catch 블록이 에러 메시지를 표시한다", async () => {
    mutateAsyncMock.mockRejectedValue(new TypeError("Network request failed"));
    render(<MfaStepUpDialog />);

    const resultPromise = openDialog();
    fireEvent.change(screen.getByPlaceholderText("6자리 코드"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "확인" }));

    await waitFor(() => expect(screen.getByText(/인증에 실패했습니다/)).toBeInTheDocument());
    expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    // 다이얼로그는 여전히 열려 있음 (재시도 없음)
    expect(screen.getByText("추가 인증이 필요합니다")).toBeInTheDocument();
    // 취소해서 promise를 resolve한다
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    await expect(resultPromise).resolves.toBe(false);
  });

  // ── 성능 단언 ─────────────────────────────────────────────────

  it("[perf] 렌더 시간预算 — 실측 ~2ms, 예산 " + RENDER_BUDGET_MS + "ms (실측 30배 이상 여유)", async () => {
    const now = performance.now();
    render(<MfaStepUpDialog />);
    // 핸들러 등록까지 완료
    act(() => {
      registeredHandler!();
    });
    const elapsed = performance.now() - now;

    expect(elapsed).toBeLessThan(perfBudgetMs(RENDER_BUDGET_MS));
  });

  // ── 게이트 적색 재현 ──────────────────────────────────────────

  it("[gate red] mutateAsync가 매우 오래 걸리면 isPending이 true가 되고 확인 버튼이 비활성화된다", async () => {
    isPendingMock.mockImplementation(() => true);
    render(<MfaStepUpDialog />);

    const resultPromise = openDialog();

    await waitFor(() => {
      const submitButton = screen.getByRole("button", { name: "확인" });
      // Button 컴포넌트는 loading=true 시 disabled를 설정함
      expect(submitButton).toBeDisabled();
    });

    // 취소하면 promise가 false로 resolve된다
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    await expect(resultPromise).resolves.toBe(false);
  });
});
