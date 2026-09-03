import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { MyApprovalRequestsPage } from "./MyApprovalRequestsPage";

const REQUEST = {
  id: 1,
  requestedAction: "LIVE_EXECUTION_ENABLE",
  scope: "PLATFORM",
  approvalMode: "SOLO",
  createdAt: "2026-09-01T00:00:00Z",
  mandatoryWaitSeconds: 0,
  firstApproverId: null,
};

let useMyApprovalRequestsResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = { data: [], isLoading: false, isError: false, error: null, refetch: vi.fn() };

const approveMutateAsync = vi.fn();
const rejectMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useMyApprovalRequests: () => useMyApprovalRequestsResult,
  useApproveMyRequest: () => ({ mutateAsync: approveMutateAsync, isPending: false }),
  useRejectMyRequest: () => ({ mutateAsync: rejectMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  useMyApprovalRequestsResult = {
    data: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
  approveMutateAsync.mockReset();
  rejectMutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <MyApprovalRequestsPage />
    </MemoryRouter>,
  );
}

// task-1161 spec §3.3: useMyApprovalRequests()가 isError를 전혀 확인하지 않아 목록
// 조회 실패가 조용히 "대기 중인 승인 요청이 없습니다"로 보였다(데이터 없음과 조회
// 실패를 구분 못 함). routeApiError(task-483)로 판정해 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(I-10 배선 증명).
describe("MyApprovalRequestsPage 목록 조회 에러 표시", () => {
  it("정상 응답이면 승인 대기 요청 카드를 렌더링한다", async () => {
    useMyApprovalRequestsResult = {
      data: [REQUEST],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("LIVE_EXECUTION_ENABLE")).toBeInTheDocument());
  });

  it("negative: AUTHZ_FORBIDDEN(403) 목록 조회 실패는 err.message 대신 권한 없음 안내를 보여준다", async () => {
    useMyApprovalRequestsResult = {
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
    expect(screen.queryByText("대기 중인 승인 요청이 없습니다.")).not.toBeInTheDocument();
  });

  it("정상 응답이지만 대기 중인 요청이 없으면 에러가 아닌 빈 상태 안내를 보여준다", async () => {
    useMyApprovalRequestsResult = {
      data: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("대기 중인 승인 요청이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("이 작업을 수행할 권한이 없습니다.")).not.toBeInTheDocument();
  });
});

// task-1161: 승인/거부는 이미 err instanceof ApiError로만 분기해 err.message를 노출하지
// 않았지만(task-911), 이 화면에서 실제 가능한 코드(404/409)로 회귀를 고정한다.
describe("MyApprovalRequestsPage 승인/거부 처리 실패 표시", () => {
  it("negative: RESOURCE_NOT_FOUND(404) 승인 실패는 항목 없음 안내를 보여준다", async () => {
    useMyApprovalRequestsResult = {
      data: [REQUEST],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    approveMutateAsync.mockRejectedValueOnce(
      new ApiError(404, "raw not found detail", "trace-2", "RESOURCE_NOT_FOUND"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    await waitFor(() =>
      expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });

  it("negative: STATE_INVALID_TRANSITION(409) 거절 실패는 err.message 대신 매핑 문구를 보여준다(자동 재시도 없음)", async () => {
    useMyApprovalRequestsResult = {
      data: [REQUEST],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    rejectMutateAsync.mockRejectedValueOnce(
      new ApiError(409, "raw transition detail", "trace-3", "STATE_INVALID_TRANSITION"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "거절" }));

    await waitFor(() =>
      expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw transition detail")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });
});
