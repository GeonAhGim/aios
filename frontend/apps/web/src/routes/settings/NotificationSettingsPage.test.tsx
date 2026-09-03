import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { NotificationSettingsPage } from "./NotificationSettingsPage";

const updateMutate = vi.fn();
let preferencesResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void } = {
  data: { execution_alert: true, dispute_update: true },
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};
let updateResult: { mutate: typeof updateMutate; isError: boolean; error: unknown } = {
  mutate: updateMutate,
  isError: false,
  error: null,
};
let historyResult: { data: unknown; isError: boolean; error: unknown; refetch: () => void } = {
  data: [],
  isError: false,
  error: null,
  refetch: vi.fn(),
};

vi.mock("@aios/shared-hooks", () => ({
  useNotificationPreferences: () => preferencesResult,
  useUpdateNotificationPreferences: () => updateResult,
  useNotificationHistory: () => historyResult,
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  updateMutate.mockReset();
  preferencesResult = {
    data: { execution_alert: true, dispute_update: true },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
  updateResult = { mutate: updateMutate, isError: false, error: null };
  historyResult = { data: [], isError: false, error: null, refetch: vi.fn() };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <NotificationSettingsPage />
    </MemoryRouter>,
  );
}

// task-1155 spec §3.3: 조회·변경 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 ForbiddenNotice/ErrorMessage 경로로만 보여준다 — 이전에는
// preferences/history 조회 실패, update.mutate 실패 모두 화면에 드러나지 않았다.
describe("NotificationSettingsPage 조회·변경 에러 표시", () => {
  it("정상 응답이면 수신 설정을 렌더링한다", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("execution_alert")).toBeInTheDocument());
  });

  it("negative: 수신 설정 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    preferencesResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw server detail", "trace-1", "AUTHZ_FORBIDDEN"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 알림 이력 조회 실패는 조용히 삼켜지지 않고 ErrorMessage 배너로 보여준다", async () => {
    historyResult = {
      data: undefined,
      isError: true,
      error: new Error("ECONNRESET"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("ECONNRESET")).toBeInTheDocument());
  });

  it("negative: 수신 설정 변경(체크박스 토글)이 실패하면 조용히 삼켜지지 않고 에러 배너를 보여준다", async () => {
    updateResult = {
      mutate: updateMutate,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-2"),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("execution_alert")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(updateMutate).toHaveBeenCalledWith({ execution_alert: false });
    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
  });
});
