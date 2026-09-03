import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { AccountDeletionPage } from "./AccountDeletionPage";

const registerWhitelistMutateAsync = vi.fn();
const requestDeletionMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useWhitelistEntries: () => ({ data: [], isLoading: false }),
  useRegisterWhitelistEntry: () => ({ mutateAsync: registerWhitelistMutateAsync, isPending: false }),
  useRequestAccountDeletion: () => ({ mutateAsync: requestDeletionMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  registerWhitelistMutateAsync.mockReset();
  requestDeletionMutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AccountDeletionPage />
    </MemoryRouter>,
  );
}

function submitWhitelistForm() {
  fireEvent.change(screen.getByLabelText("출금 목적지 주소"), { target: { value: "0xabc" } });
  fireEvent.change(screen.getAllByLabelText("비밀번호 확인")[0], { target: { value: "pw123456" } });
  fireEvent.click(screen.getByRole("button", { name: "목적지 등록" }));
}

function submitDeleteForm() {
  fireEvent.change(screen.getAllByLabelText("비밀번호 확인")[1], { target: { value: "pw123456" } });
  fireEvent.click(screen.getByRole("button", { name: "탈퇴 요청" }));
}

// task-930 §3.3: 화이트리스트 등록·탈퇴 요청 실패는 err.message를 직접 노출하지
// 않고 routeApiError(task-483)로 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage
// 경로로만 보여준다.
describe("AccountDeletionPage 화이트리스트 등록 에러 표시", () => {
  it("negative: 미지 error_code(서버 상세 메시지 없음)는 안전한 기본 안내 문구로 수렴한다", async () => {
    registerWhitelistMutateAsync.mockRejectedValue(new ApiError(402, "", "trace-wl-1", "X_UNMAPPED_CODE"));
    renderPage();

    submitWhitelistForm();

    await waitFor(() =>
      expect(
        screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
  });

  it("negative: AUTH_INVALID_CREDENTIALS(401)는 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", async () => {
    registerWhitelistMutateAsync.mockRejectedValue(
      new ApiError(401, "raw server detail", "trace-wl-2", "AUTH_INVALID_CREDENTIALS"),
    );
    renderPage();

    submitWhitelistForm();

    await waitFor(() =>
      expect(screen.getByText("이메일 또는 비밀번호가 올바르지 않습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    registerWhitelistMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitWhitelistForm();

    await waitFor(() => expect(screen.getByText("등록에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});

describe("AccountDeletionPage 탈퇴 요청 에러 표시", () => {
  it("negative: STATE_INVALID_TRANSITION(409, RUNNING 실행 존재)는 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", async () => {
    requestDeletionMutateAsync.mockRejectedValue(
      new ApiError(409, "execution exec-1 is RUNNING", "trace-del-1", "STATE_INVALID_TRANSITION"),
    );
    renderPage();

    submitDeleteForm();

    await waitFor(() =>
      expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("execution exec-1 is RUNNING")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    requestDeletionMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitDeleteForm();

    await waitFor(() => expect(screen.getByText("탈퇴 요청에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
