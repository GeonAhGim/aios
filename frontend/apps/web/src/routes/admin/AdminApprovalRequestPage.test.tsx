import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { AdminApprovalRequestPage } from "./AdminApprovalRequestPage";

const approveMutateAsync = vi.fn();
const rejectMutateAsync = vi.fn();
let pendingData: unknown[] = [];

vi.mock("@aios/shared-hooks", () => ({
  usePendingApprovalRequests: () => ({ data: pendingData, isLoading: false }),
  useApproveRequest: () => ({ mutateAsync: approveMutateAsync, isPending: false }),
  useRejectRequest: () => ({ mutateAsync: rejectMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  approveMutateAsync.mockReset();
  rejectMutateAsync.mockReset();
  pendingData = [];
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminApprovalRequestPage />
    </MemoryRouter>,
  );
}

function requestIdInput(container: HTMLElement): HTMLInputElement {
  return container.querySelector('input[type="number"]') as HTMLInputElement;
}

function submitManualApprove(container: HTMLElement, requestId: string) {
  fireEvent.change(requestIdInput(container), { target: { value: requestId } });
  fireEvent.click(screen.getAllByRole("button", { name: "승인" })[0]);
}

// task-911 §3.3: 승인/거절 처리 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("AdminApprovalRequestPage 처리 에러 표시", () => {
  it("negative: AUTH_MFA_REQUIRED(403)는 err.message 대신 ForbiddenNotice의 step-up 안내를 보여준다", async () => {
    approveMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "AUTH_MFA_REQUIRED"),
    );
    const { container } = renderPage();

    submitManualApprove(container, "42");

    await waitFor(() => expect(screen.getByText("추가 인증이 필요합니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: AUTH_TENANT_MISMATCH(403)는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    approveMutateAsync.mockRejectedValue(
      new ApiError(403, "raw tenant detail", "trace-2", "AUTH_TENANT_MISMATCH"),
    );
    const { container } = renderPage();

    submitManualApprove(container, "42");

    await waitFor(() =>
      expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw tenant detail")).not.toBeInTheDocument();
  });

  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    rejectMutateAsync.mockRejectedValue(
      new ApiError(403, "raw policy detail", "trace-3", "POLICY_LIVE_BLOCKED"),
    );
    const { container } = renderPage();

    fireEvent.change(requestIdInput(container), { target: { value: "7" } });
    fireEvent.click(screen.getAllByRole("button", { name: "거절" })[0]);

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw policy detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    approveMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitManualApprove(container, "42");

    await waitFor(() => expect(screen.getByText("처리에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
