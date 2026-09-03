import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { AdminRoute } from "../../components/AdminRoute";
import { AdminHomePage } from "./AdminHomePage";

let meResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void } = {
  data: { email: "admin@example.com", isPlatformAdmin: true },
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => meResult,
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  meResult = {
    data: { email: "admin@example.com", isPlatformAdmin: true },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
});

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route
          path="/admin"
          element={
            <AdminRoute>
              <AdminHomePage />
            </AdminRoute>
          }
        />
        <Route path="/dashboard" element={<div>DASHBOARD_PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

// task-1155: AdminHomePage 자체는 정적 내비게이션 허브라 조회·변경 API가 없다
// (note 참조) — 이 화면에 실제로 도달·표시하는 유일한 실패 지점은 진입을
// 감싸는 AdminRoute(useMe())이며, 이전에는 isError 상태를 확인하지 않고
// !me?.isPlatformAdmin 분기로 흘러 조용히 /dashboard로 리다이렉트했다.
describe("AdminHomePage 진입 에러 표시(AdminRoute 경유)", () => {
  it("관리자면 섹션 링크를 렌더링한다", async () => {
    renderRoute();
    await waitFor(() => expect(screen.getByText("전략 검수 대기열")).toBeInTheDocument());
    expect(screen.getByText("승인 요청 처리")).toBeInTheDocument();
  });

  it("negative: useMe() 조회가 500으로 실패하면 조용히 /dashboard로 리다이렉트하지 않고 ErrorMessage를 보여준다", async () => {
    meResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-1"),
      refetch: vi.fn(),
    };
    renderRoute();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("DASHBOARD_PAGE")).not.toBeInTheDocument();
  });

  it("negative: useMe() 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    meResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw server detail", "trace-2", "AUTHZ_FORBIDDEN"),
      refetch: vi.fn(),
    };
    renderRoute();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.queryByText("DASHBOARD_PAGE")).not.toBeInTheDocument();
  });

  it("관리자가 아니면(에러 없이 isPlatformAdmin=false) 여전히 /dashboard로 리다이렉트한다", async () => {
    meResult = {
      data: { email: "user@example.com", isPlatformAdmin: false },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    renderRoute();

    await waitFor(() => expect(screen.getByText("DASHBOARD_PAGE")).toBeInTheDocument());
  });
});
