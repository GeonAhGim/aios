import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { UserManagementPage } from "./UserManagementPage";

const changeStatusMutate = vi.fn();
const suspendSellerMutate = vi.fn();
const refetchUsers = vi.fn();

const USER = {
  userId: "u1",
  email: "seller@example.com",
  status: "ACTIVE",
  createdAt: "2026-09-01T00:00:00Z",
};

let usersResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: [USER],
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchUsers,
};

vi.mock("@aios/shared-hooks", () => ({
  useAdminUsers: () => usersResult,
  useChangeUserStatus: () => ({ mutate: changeStatusMutate }),
  useSuspendSeller: () => ({ mutate: suspendSellerMutate }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  changeStatusMutate.mockReset();
  suspendSellerMutate.mockReset();
  refetchUsers.mockReset();
  usersResult = {
    data: [USER],
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchUsers,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <UserManagementPage />
    </MemoryRouter>,
  );
}

function clickSuspend() {
  fireEvent.click(screen.getByRole("button", { name: "판매정지" }));
}

// task-1156 §3.3: 지금까지 changeStatus.mutate/suspendSeller.mutate가 콜백 없이
// 호출돼 실패를 완전히 조용히 삼켰다 — 에러 상태 자체가 없었다. 이 화면에서
// 실제 가능한 코드(403/404/409)를 각각 ForbiddenNotice/ErrorMessage 경로로
// 표면화한다.
describe("UserManagementPage 목록 조회 실패/빈 상태 표시", () => {
  it("negative: 목록 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    usersResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-users-1"),
      refetch: refetchUsers,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("사용자가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    usersResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden list detail", "trace-users-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchUsers,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden list detail")).not.toBeInTheDocument();
    expect(screen.queryByText("사용자가 없습니다.")).not.toBeInTheDocument();
  });

  it("positive: 목록이 실제로 비어 있으면(에러 없이 data=[]) 빈 상태를 보여준다", async () => {
    usersResult = { data: [], isLoading: false, isError: false, error: null, refetch: refetchUsers };
    renderPage();

    await waitFor(() => expect(screen.getByText("사용자가 없습니다.")).toBeInTheDocument());
  });
});

describe("UserManagementPage 사용자 조치 실패 표시", () => {
  it("negative: AUTHZ_FORBIDDEN(403) 판매정지 실패는 권한 없음 안내를 보여준다", async () => {
    suspendSellerMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden detail", "trace-1", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    clickSuspend();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("negative: RESOURCE_NOT_FOUND(404) 판매정지 실패는 항목 없음 안내를 보여준다", async () => {
    suspendSellerMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(404, "raw not found detail", "trace-2", "RESOURCE_NOT_FOUND"));
    });
    renderPage();

    clickSuspend();

    await waitFor(() =>
      expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });

  it("negative: STATE_INVALID_TRANSITION(409) 상태변경 실패는 err.message 대신 매핑 문구를 보여준다", async () => {
    changeStatusMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(409, "raw transition detail", "trace-3", "STATE_INVALID_TRANSITION"),
      );
    });
    renderPage();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "SUSPENDED" } });

    await waitFor(() =>
      expect(
        screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw transition detail")).not.toBeInTheDocument();
  });
});

// useSuspendSeller/useChangeUserStatus는 성공 시 실제로 ["adminUsers"] 쿼리를
// invalidate해 목록을 재조회한다(packages/shared-hooks/src/useAdmin.ts) — 여기서는
// 그 재조회 결과가 실제로 반영되면 화면이 갱신되는지를 실제 DOM으로 확인한다.
describe("UserManagementPage 조치 성공 시 목록 갱신", () => {
  it("판매정지 성공 후 재조회 결과가 반영되면 상태 배지가 SUSPENDED로 갱신된다", async () => {
    const { rerender } = renderPage();

    clickSuspend();

    usersResult = {
      data: [{ ...USER, status: "SUSPENDED" }],
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchUsers,
    };
    rerender(
      <MemoryRouter>
        <UserManagementPage />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText("SUSPENDED", { selector: "span" })).toBeInTheDocument(),
    );
    expect(screen.queryByText("ACTIVE", { selector: "span" })).not.toBeInTheDocument();
  });

  it("상태변경 성공 후 재조회 결과가 반영되면 상태 배지가 SUSPENDED로 갱신된다", async () => {
    const { rerender } = renderPage();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "SUSPENDED" } });

    usersResult = {
      data: [{ ...USER, status: "SUSPENDED" }],
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchUsers,
    };
    rerender(
      <MemoryRouter>
        <UserManagementPage />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText("SUSPENDED", { selector: "span" })).toBeInTheDocument(),
    );
    expect(screen.queryByText("ACTIVE", { selector: "span" })).not.toBeInTheDocument();
  });
});
