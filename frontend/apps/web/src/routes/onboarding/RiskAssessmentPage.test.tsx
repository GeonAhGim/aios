import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { RiskAssessmentPage } from "./RiskAssessmentPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useSubmitRiskAssessment: () => ({ mutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/onboarding/risk-assessment"]}>
      <Routes>
        <Route path="/onboarding/risk-assessment" element={<RiskAssessmentPage />} />
        <Route path="/dashboard" element={<div>대시보드 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitForm() {
  fireEvent.click(screen.getByRole("button", { name: "제출하기" }));
}

// task-902 §3.3/§3.4: 제출 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage
// 경로로만 보여준다.
describe("RiskAssessmentPage 제출 에러 표시", () => {
  it("제출 성공 시 /dashboard로 이동한다", async () => {
    mutateAsync.mockResolvedValue({ riskProfile: "MODERATE" });
    renderPage();

    submitForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
  });

  // negative: 작성 중 액세스 토큰이 만료되면 401 AUTH_TOKEN_EXPIRED로 거부될
  // 수 있다 — isSessionExpiredErrorCode(task-354)가 잡는 갈래를 raw message
  // 대신 ErrorMessage의 매핑 문구로 보여준다.
  it("negative: 401 AUTH_TOKEN_EXPIRED는 raw message 대신 ErrorMessage의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(401, "raw server detail", undefined, "AUTH_TOKEN_EXPIRED"),
    );
    renderPage();

    submitForm();

    await waitFor(() =>
      expect(screen.getByText("세션이 만료되었습니다. 다시 로그인해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitForm();

    await waitFor(() => expect(screen.getByText("평가 제출에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
