import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ApprovalSettingsPage } from "./ApprovalSettingsPage";

const updateMutateAsync = vi.fn();
let settingsData: unknown = { mode: "SOLO", mandatoryWaitSeconds: 60 };

vi.mock("@aios/shared-hooks", () => ({
  useApprovalSettings: () => ({ data: settingsData, isLoading: false }),
  useUpdateApprovalSettings: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  updateMutateAsync.mockReset();
  settingsData = { mode: "SOLO", mandatoryWaitSeconds: 60 };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ApprovalSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// task-911 §3.3: 승인 방식 설정 변경 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만
// 보여준다 — 위험등급 경고(RiskWarningModal)만 예외로 메시지 문구 매칭을 유지한다.
describe("ApprovalSettingsPage 저장 에러 표시", () => {
  it("negative: AUTH_MFA_REQUIRED(403)는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    updateMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "AUTH_MFA_REQUIRED"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(screen.getByText("추가 인증이 필요합니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    updateMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(screen.getByText("설정 변경에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("위험등급 불일치(400)는 배너가 아니라 RiskWarningModal로 사유를 보여준다", async () => {
    updateMutateAsync.mockRejectedValue(
      new ApiError(400, "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.", undefined),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(screen.getByText("위험등급 불일치 경고")).toBeInTheDocument());
    expect(
      screen.getByText("회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다."),
    ).toBeInTheDocument();
  });

  it("VALIDATION_INVALID_FIELD가 아닌 그 외 400은 BadRequestNotice 경로로 안내한다", async () => {
    updateMutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});
