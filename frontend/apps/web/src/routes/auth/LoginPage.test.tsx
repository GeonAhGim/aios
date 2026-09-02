import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { LoginPage } from "./LoginPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useLogin: () => ({ mutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/wallet" element={<div>지갑 페이지</div>} />
        <Route path="/dashboard" element={<div>대시보드 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitLoginForm() {
  fireEvent.change(screen.getByLabelText("이메일"), { target: { value: "a@example.com" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw123456" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
}

// task-354: ProtectedRoute가 세션 만료·미로그인 시 남긴 ?next=<원경로>로
// 로그인 성공 후 복귀하는지 확인한다.
describe("LoginPage next 복귀", () => {
  it("?next=/wallet로 진입해 로그인에 성공하면 /wallet로 복귀한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-1" });
    renderAt("/login?next=%2Fwallet");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("지갑 페이지")).toBeInTheDocument());
  });

  it("next가 없으면 기본값 /dashboard로 이동한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-2" });
    renderAt("/login");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });

  // negative: 외부 사이트로 여는 open-redirect를 막는다 — "//evil.example"은
  // 프로토콜 상대 URL로 해석돼 origin이 바뀔 수 있으므로 신뢰하지 않는다.
  it("negative: next가 프로토콜 상대 URL(//)이면 기본값 /dashboard로 대체한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-3" });
    renderAt("/login?next=%2F%2Fevil.example");

    submitLoginForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });
});

// task-387: PLT-22 AUTH_ACCOUNT_LOCKED(423) — 잠금 동안 입력·제출을 막고 남은 초를
// 보여주다가 0이 되면 자동으로 해제한다.
describe("LoginPage 계정 잠금(AUTH_ACCOUNT_LOCKED)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("423 + retry_after_seconds가 있으면 잠기고 정확한 초를 보여준다", async () => {
    vi.useFakeTimers();
    mutateAsync.mockRejectedValue(new ApiError(423, "잠김", undefined, "AUTH_ACCOUNT_LOCKED", 45));
    renderAt("/login");

    await act(async () => submitLoginForm());

    expect(screen.getByText("45초 후 다시 시도할 수 있습니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("이메일")).toBeDisabled();
    expect(screen.getByLabelText("비밀번호")).toBeDisabled();
    expect(screen.getByRole("button", { name: "로그인" })).toBeDisabled();
  });

  it("retry_after_seconds가 없으면 기본 60초로 잠근다", async () => {
    vi.useFakeTimers();
    mutateAsync.mockRejectedValue(new ApiError(423, "잠김", undefined, "AUTH_ACCOUNT_LOCKED"));
    renderAt("/login");

    await act(async () => submitLoginForm());

    expect(screen.getByText("60초 후 다시 시도할 수 있습니다.")).toBeInTheDocument();
  });

  it("카운트다운이 0이 되면 자동으로 해제되어 재제출할 수 있다", async () => {
    vi.useFakeTimers();
    mutateAsync.mockRejectedValueOnce(new ApiError(423, "잠김", undefined, "AUTH_ACCOUNT_LOCKED", 2));
    renderAt("/login");

    await act(async () => submitLoginForm());
    expect(screen.getByRole("button", { name: "로그인" })).toBeDisabled();

    await act(async () => vi.advanceTimersByTime(1000));
    expect(screen.getByText("1초 후 다시 시도할 수 있습니다.")).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTime(1000));
    expect(screen.queryByText(/초 후 다시 시도할 수 있습니다\./)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그인" })).toBeEnabled();
    expect(screen.getByLabelText("이메일")).toBeEnabled();

    vi.useRealTimers();
    mutateAsync.mockResolvedValueOnce({ accessToken: "t-4" });
    submitLoginForm();
    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });

  it("401 등 잠금 외 에러는 잠그지 않는다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(401, "이메일 또는 비밀번호가 올바르지 않습니다.", undefined, "AUTH_INVALID_CREDENTIALS"),
    );
    renderAt("/login");

    submitLoginForm();

    await waitFor(() =>
      expect(screen.getByText("이메일 또는 비밀번호가 올바르지 않습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/초 후 다시 시도할 수 있습니다\./)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그인" })).toBeEnabled();
  });

  it("unmount 시 카운트다운 타이머를 정리한다", async () => {
    vi.useFakeTimers();
    mutateAsync.mockRejectedValue(new ApiError(423, "잠김", undefined, "AUTH_ACCOUNT_LOCKED", 30));
    const { unmount } = render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await act(async () => submitLoginForm());
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
