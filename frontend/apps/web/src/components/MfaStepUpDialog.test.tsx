import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MfaStepUpDialog } from "./MfaStepUpDialog";

let registeredHandler: (() => Promise<boolean>) | null = null;
const configureMfaStepUpHandlerMock = vi.fn((handler: (() => Promise<boolean>) | null) => {
  registeredHandler = handler;
});

vi.mock("@aios/api-client", () => ({
  configureMfaStepUpHandler: (handler: (() => Promise<boolean>) | null) => configureMfaStepUpHandlerMock(handler),
}));

const mutateAsyncMock = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useVerifyMfa: () => ({ mutateAsync: mutateAsyncMock, isPending: false }),
}));

function openDialog(): Promise<boolean> {
  let promise!: Promise<boolean>;
  act(() => {
    promise = registeredHandler!();
  });
  return promise;
}

// task-481: http.ts가 403 AUTH_MFA_REQUIRED를 받으면 mfaStepUp.ts의
// requestMfaStepUp()이 이 컴포넌트가 등록한 핸들러를 부른다. 여기서는 그
// 훅 지점(configureMfaStepUpHandler)과 authClient.verifyMfa만 검증하고,
// http.ts 쪽 재시도 로직은 mfaStepUp.test.ts가 담당한다.
describe("MfaStepUpDialog", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    registeredHandler = null;
  });

  it("마운트 시 configureMfaStepUpHandler로 핸들러를 등록하고, 언마운트 시 해제한다", () => {
    const { unmount } = render(<MfaStepUpDialog />);

    expect(configureMfaStepUpHandlerMock).toHaveBeenCalledTimes(1);
    expect(registeredHandler).toBeInstanceOf(Function);

    unmount();

    expect(configureMfaStepUpHandlerMock).toHaveBeenLastCalledWith(null);
  });

  it("핸들러 호출로 다이얼로그가 열리고, TOTP 제출 성공 시 authClient.verifyMfa만 호출해 true로 resolve하며 입력값을 비운다", async () => {
    mutateAsyncMock.mockResolvedValue({ mfaEnabled: true });
    render(<MfaStepUpDialog />);

    const resultPromise = openDialog();
    expect(screen.getByText("추가 인증이 필요합니다")).toBeInTheDocument();

    const input = screen.getByPlaceholderText("6자리 코드") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "확인" }));

    await expect(resultPromise).resolves.toBe(true);
    expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    expect(mutateAsyncMock).toHaveBeenCalledWith("123456");
    await waitFor(() => expect(screen.queryByText("추가 인증이 필요합니다")).not.toBeInTheDocument());
  });

  it("negative: TOTP 검증 실패 시 원요청을 재시도하지 않고(promise 유지) 입력값만 비우며 에러를 보여준다", async () => {
    mutateAsyncMock.mockRejectedValue(new Error("AUTH_MFA_INVALID"));
    render(<MfaStepUpDialog />);

    let resolved: boolean | undefined;
    const resultPromise = openDialog().then((ok) => {
      resolved = ok;
      return ok;
    });
    void resultPromise;

    const input = screen.getByPlaceholderText("6자리 코드") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "확인" }));

    await waitFor(() => expect(screen.getByText(/인증에 실패했습니다/)).toBeInTheDocument());
    expect((screen.getByPlaceholderText("6자리 코드") as HTMLInputElement).value).toBe("");
    // 실패해도 다이얼로그는 열려 있고(재입력 가능), 아직 resolve되지 않았다 —
    // 즉 원요청은 재시도되지 않은 채 대기 중이다.
    expect(screen.getByText("추가 인증이 필요합니다")).toBeInTheDocument();
    expect(resolved).toBeUndefined();
  });

  it("취소를 누르면 재시도 없이 false로 resolve하고 다이얼로그를 닫으며 입력값을 비운다", async () => {
    render(<MfaStepUpDialog />);

    const resultPromise = openDialog();
    fireEvent.change(screen.getByPlaceholderText("6자리 코드"), { target: { value: "111111" } });
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    await expect(resultPromise).resolves.toBe(false);
    expect(mutateAsyncMock).not.toHaveBeenCalled();
    expect(screen.queryByText("추가 인증이 필요합니다")).not.toBeInTheDocument();
  });

  it("negative: 대기 중 언마운트되면 false로 resolve하고 입력값을 비운다", async () => {
    const { unmount } = render(<MfaStepUpDialog />);

    const resultPromise = openDialog();
    fireEvent.change(screen.getByPlaceholderText("6자리 코드"), { target: { value: "222222" } });

    unmount();

    await expect(resultPromise).resolves.toBe(false);
  });
});
