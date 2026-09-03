import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { MfaSetupPage } from "./MfaSetupPage";

vi.mock("qrcode", () => ({
  default: { toDataURL: vi.fn().mockResolvedValue("data:image/png;base64,stub") },
}));

const setupMutate = vi.fn();
const verifyMutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useSetupMfa: () => ({ mutate: setupMutate, isPending: false }),
  useVerifyMfa: () => ({ mutateAsync: verifyMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  setupMutate.mockReset();
  verifyMutateAsync.mockReset();
});

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/onboarding/mfa-setup"]}>
      <Routes>
        <Route path="/onboarding/mfa-setup" element={<MfaSetupPage />} />
        <Route path="/onboarding/risk-assessment" element={<div>적합성평가 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

// task-902 §3.3/§3.4: 설정·검증 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 ForbiddenNotice/BadRequestNotice/ErrorMessage
// 경로로만 보여준다.
describe("MfaSetupPage 설정 발급 에러 표시", () => {
  // negative: 이미 MFA가 켜진 계정이 비밀번호 없이 재발급을 요청하면
  // 403 AUTH_MFA_REQUIRED로 거부된다(auth.py setup_mfa) — raw message 대신
  // ForbiddenNotice의 매핑 문구를 보여준다.
  it("negative: 403 AUTH_MFA_REQUIRED는 raw message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    setupMutate.mockImplementation((_vars, { onError }) => {
      onError(new ApiError(403, "raw server detail", undefined, "AUTH_MFA_REQUIRED"));
    });
    renderPage();

    await waitFor(() => expect(screen.getByText("추가 인증이 필요합니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("설정 발급이 ApiError가 아닌 실패로 끝나면 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    setupMutate.mockImplementation((_vars, { onError }) => {
      onError(new Error("ECONNRESET"));
    });
    renderPage();

    await waitFor(() => expect(screen.getByText("설정 발급에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});

describe("MfaSetupPage 검증 에러 표시", () => {
  function renderWithQr() {
    setupMutate.mockImplementation((_vars, { onSuccess }) => {
      onSuccess({ secret: "SECRET123", provisioningUri: "otpauth://totp/aios" });
    });
    renderPage();
  }

  async function submitVerifyForm() {
    await waitFor(() => expect(screen.getByPlaceholderText("6자리 코드")).not.toBeDisabled());
    fireEvent.change(screen.getByPlaceholderText("6자리 코드"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "인증 완료" }));
  }

  // negative: 코드가 틀리면 400 AUTH_MFA_INVALID로 거부된다 — raw message
  // 대신 BadRequestNotice의 매핑 문구를 보여준다.
  it("negative: 400 AUTH_MFA_INVALID는 raw message 대신 BadRequestNotice의 매핑 문구를 보여준다", async () => {
    renderWithQr();
    verifyMutateAsync.mockRejectedValue(
      new ApiError(400, "raw server detail", undefined, "AUTH_MFA_INVALID"),
    );

    await submitVerifyForm();

    await waitFor(() =>
      expect(screen.getByText("인증 코드가 올바르지 않습니다. 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("검증 성공 시 /onboarding/risk-assessment로 이동한다", async () => {
    renderWithQr();
    verifyMutateAsync.mockResolvedValue({ mfaEnabled: true });

    await submitVerifyForm();

    await waitFor(() => expect(screen.getByText("적합성평가 페이지")).toBeInTheDocument());
  });

  it("검증이 ApiError가 아닌 실패로 끝나면 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    renderWithQr();
    verifyMutateAsync.mockRejectedValue(new Error("ECONNRESET"));

    await submitVerifyForm();

    await waitFor(() => expect(screen.getByText("인증에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
