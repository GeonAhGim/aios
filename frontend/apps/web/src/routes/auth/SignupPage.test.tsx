import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { SignupPage } from "./SignupPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useSignup: () => ({ mutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  vi.useRealTimers();
});

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/signup"]}>
      <Routes>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/onboarding/mfa-setup" element={<div>MFA 설정 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitSignupForm() {
  fireEvent.change(screen.getByLabelText("이메일"), { target: { value: "a@example.com" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw123456789!" } });
  fireEvent.click(screen.getByRole("button", { name: "가입하기" }));
}

// task-902 §3.3/§3.4: 회원가입 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage/잠금
// 안내(423) 경로로만 보여준다.
describe("SignupPage 에러 표시", () => {
  it("가입 성공 시 /onboarding/mfa-setup으로 이동한다", async () => {
    mutateAsync.mockResolvedValue({ accessToken: "t-1" });
    renderPage();

    submitSignupForm();

    await waitFor(() => expect(screen.getByText("MFA 설정 페이지")).toBeInTheDocument());
  });

  // negative: AUTH_ACCOUNT_LOCKED(423)은 LoginPage(task-387)와 동일하게
  // deriveLockout으로 잠그고 raw message 대신 카운트다운 안내를 보여준다.
  it("negative: 423 AUTH_ACCOUNT_LOCKED는 raw message 대신 잠금 카운트다운을 보여주고 입력을 막는다", async () => {
    vi.useFakeTimers();
    mutateAsync.mockRejectedValue(
      new ApiError(423, "raw lock detail", undefined, "AUTH_ACCOUNT_LOCKED", 20),
    );
    renderPage();

    await act(async () => submitSignupForm());

    expect(screen.getByText("20초 후 다시 시도할 수 있습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw lock detail")).not.toBeInTheDocument();
    expect(screen.getByLabelText("이메일")).toBeDisabled();
    expect(screen.getByRole("button", { name: "가입하기" })).toBeDisabled();
  });

  it("negative: VALIDATION_*(400) 실패는 err.message 대신 BadRequestNotice의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw server detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    renderPage();

    submitSignupForm();

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitSignupForm();

    await waitFor(() => expect(screen.getByText("회원가입에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  // task-943: classifyBadRequest가 VALIDATION_INVALID_FIELD를 "field"로 분류해
  // BadRequestNotice가 스스로 null을 렌더한다(task-364) — 지금까지 이 경로는
  // 배너도 인라인도 없이 완전히 조용했다. useFieldErrors가 details.fields[]를
  // 읽어 입력 옆에 인라인 오류를 보여준다.
  it("VALIDATION_INVALID_FIELD(400): details.fields[]를 해당 입력 옆에 인라인 오류로 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.email", "body.password"],
      }),
    );
    renderPage();

    submitSignupForm();

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );
  });

  it("필드를 수정하면 clearField로 그 필드 오류만 사라지고 나머지는 유지된다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.email", "body.password"],
      }),
    );
    renderPage();

    submitSignupForm();
    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );

    fireEvent.change(screen.getByLabelText("이메일"), { target: { value: "b@example.com" } });

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(1),
    );
  });
});
