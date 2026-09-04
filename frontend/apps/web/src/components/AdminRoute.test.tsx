import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { AdminRoute } from "./AdminRoute";

// task-1197: §3.5 is_platform_admin fail-closed 회귀. AdminHomePage.test.tsx가
// 이미 검증한 useMe() 목 패턴(mutable meResult)을 그대로 재사용해 AdminRoute
// 자체의 분기(로딩/에러/비관리자/관리자)를 직접 겨눈다.
interface MeQueryResult {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

let meResult: MeQueryResult = {
  data: undefined,
  isLoading: true,
  isError: false,
  error: null,
  refetch: vi.fn(),
};

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => meResult,
}));

function setMe(overrides: Partial<MeQueryResult>) {
  meResult = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  setMe({ isLoading: true });
});

function tree() {
  return (
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route
          path="/admin"
          element={
            <AdminRoute>
              <div>ADMIN_CONTENT</div>
            </AdminRoute>
          }
        />
        <Route path="/dashboard" element={<div>DASHBOARD_PAGE</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("AdminRoute — §3.5 fail-closed", () => {
  it("권한 미확정(로딩) 상태에서는 관리자 화면을 노출하지 않는다", () => {
    setMe({ isLoading: true });
    render(tree());

    expect(screen.getByText("로딩 중...")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
    expect(screen.queryByText("DASHBOARD_PAGE")).not.toBeInTheDocument();
  });

  it("로딩 → 비관리자로 판정이 확정되는 전 과정에서 관리자 화면이 단 한 번도 렌더되지 않는다", () => {
    setMe({ isLoading: true });
    const { rerender } = render(tree());
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();

    setMe({ isLoading: false, data: { isPlatformAdmin: false } });
    rerender(tree());

    expect(screen.getByText("DASHBOARD_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
  });

  it("negative: useMe() 조회가 500으로 실패하면 조용히 /dashboard로 보내지 않고 ErrorMessage를 보여준다(관리자 화면도 노출하지 않는다)", () => {
    setMe({ isError: true, error: new ApiError(500, "일시적인 오류입니다.", "trace-1") });
    render(tree());

    expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
    expect(screen.queryByText("DASHBOARD_PAGE")).not.toBeInTheDocument();
  });

  it("negative: useMe() 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여주고 관리자 화면은 노출하지 않는다", () => {
    setMe({ isError: true, error: new ApiError(403, "raw server detail", "trace-2", "AUTHZ_FORBIDDEN") });
    render(tree());

    expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
  });

  it("negative: 에러도 로딩도 아닌데 me가 없는 정합성 어긋난 상태에서도 최소권한으로 /dashboard로 보내고 관리자 화면은 노출하지 않는다", () => {
    setMe({ data: undefined });
    render(tree());

    expect(screen.getByText("DASHBOARD_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
  });

  it("관리자가 아니면(에러 없이 isPlatformAdmin=false) /dashboard로 보내고 관리자 화면은 노출하지 않는다", () => {
    setMe({ data: { isPlatformAdmin: false } });
    render(tree());

    expect(screen.getByText("DASHBOARD_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN_CONTENT")).not.toBeInTheDocument();
  });

  it("플랫폼 관리자면 관리자 화면을 렌더한다", () => {
    setMe({ data: { isPlatformAdmin: true } });
    render(tree());

    expect(screen.getByText("ADMIN_CONTENT")).toBeInTheDocument();
  });
});
