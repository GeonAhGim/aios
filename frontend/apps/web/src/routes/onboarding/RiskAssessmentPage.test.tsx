import "../../i18n";
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
  window.sessionStorage.clear();
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

// F-2(task-10642): 온보딩 단계가 전진 전용이라 되돌릴 수 없었다 — 이전 단계(MFA
// 설정)로 돌아가는 링크가 항상 보여야 한다.
describe("RiskAssessmentPage 이전 단계 링크(F-2)", () => {
  it("2단계 인증(/onboarding/mfa-setup)으로 돌아가는 링크를 보여준다", () => {
    renderPage();

    const link = screen.getByRole("link", { name: "← 이전 단계(2단계 인증)로 돌아가기" });
    expect(link).toHaveAttribute("href", "/onboarding/mfa-setup");
  });
});

// F-3(task-10642): 제출 시점에 세션이 만료(401 AUTH_TOKEN_EXPIRED)되면 재로그인 후
// 같은 화면으로 돌아와도 입력값이 모두 사라졌다 — sessionStorage draft로 평가
// 응답(비밀값 아님)만 복원한다.
describe("RiskAssessmentPage 입력값 복원(F-3)", () => {
  it("이전에 남긴 draft가 있으면 마운트 시 입력값을 복원하고 복원 안내를 보여준다", () => {
    window.sessionStorage.setItem(
      "aios.riskAssessment.draft.v1",
      JSON.stringify({
        yearsOfExperience: 7,
        investableRatioPct: 42,
        lossTolerancePct: 33,
        investmentGoal: "SHORT_TERM_PROFIT",
        liquidityNeed: "OVER_3_YEARS",
      }),
    );

    renderPage();

    expect(screen.getByText("세션 만료로 중단됐던 입력값을 복원했습니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("투자 경험 연수")).toHaveValue(7);
  });

  it("입력을 바꾸면 sessionStorage draft가 최신 값으로 갱신된다", () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("투자 경험 연수"), { target: { value: "12" } });

    const saved = JSON.parse(window.sessionStorage.getItem("aios.riskAssessment.draft.v1") ?? "{}");
    expect(saved.yearsOfExperience).toBe(12);
  });

  it("negative: draft에는 secret/passphrase 같은 비밀값이 포함되지 않는다 — 저장 키는 평가 응답 5개 필드뿐", () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("투자 경험 연수"), { target: { value: "3" } });

    const saved = JSON.parse(window.sessionStorage.getItem("aios.riskAssessment.draft.v1") ?? "{}");
    expect(Object.keys(saved).sort()).toEqual(
      [
        "investableRatioPct",
        "investmentGoal",
        "liquidityNeed",
        "lossTolerancePct",
        "yearsOfExperience",
      ].sort(),
    );
  });

  it("제출에 성공하면 draft를 지운다", async () => {
    mutateAsync.mockResolvedValue({ riskProfile: "MODERATE" });
    renderPage();

    submitForm();

    await waitFor(() => expect(screen.getByText("대시보드 페이지")).toBeInTheDocument());
    expect(window.sessionStorage.getItem("aios.riskAssessment.draft.v1")).toBeNull();
  });
});

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
