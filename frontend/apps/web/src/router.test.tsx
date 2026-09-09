import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@aios/shared-hooks";

interface MeShape {
  email?: string;
  mfaEnabled?: boolean;
  isPlatformAdmin?: boolean;
}

let meResult: { data: MeShape | undefined; isLoading: boolean } = { data: undefined, isLoading: false };
let riskResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false };

// router.tsx는 46개 페이지를 전부 정적 import한다 — 그중 실제로 마운트되는 페이지
// (DashboardPage/AppShell)가 쓰는 훅만 통제 가능한 목으로 바꾸고, useAuthStore는
// ProtectedRoute.test.tsx와 같은 이유로 실제 스토어를 그대로 둔다(가드 배선 자체가
// 이 파일의 테스트 대상이므로, 스토어까지 목으로 바꾸면 실제 로그인 상태 판단이
// 빠진 동어반복이 된다).
vi.mock("@aios/shared-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/shared-hooks")>();
  return {
    ...actual,
    useMe: () => meResult,
    useRiskProfile: () => riskResult,
    usePortfolio: () => ({ data: undefined, isLoading: false }),
    useExecutions: () => ({ data: undefined, isLoading: false }),
    useLogout: () => vi.fn(),
    useLogin: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

const { router } = await import("./router");

function renderRouter() {
  return render(<RouterProvider router={router} />);
}

function loginFullyOnboarded() {
  act(() => useAuthStore.getState().setToken("token-1"));
  meResult = { data: { email: "user@example.com", mfaEnabled: true, isPlatformAdmin: false }, isLoading: false };
  riskResult = { data: { riskProfile: "MODERATE" }, isLoading: false };
}

afterEach(() => {
  cleanup();
  act(() => {
    useAuthStore.getState().logout();
  });
  meResult = { data: undefined, isLoading: false };
  riskResult = { data: undefined, isLoading: false };
});

describe("router — 경로별 가드 배선(protect/protectAdmin)", () => {
  it("정상 렌더: 온보딩을 마친 사용자가 보호 경로(/dashboard)에 접근하면 실제 대시보드 화면이 뜬다", async () => {
    loginFullyOnboarded();

    await act(async () => {
      await router.navigate("/dashboard");
    });
    renderRouter();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "대시보드" })).toBeInTheDocument();
    });
    expect(screen.getByText("실행 중인 전략이 없습니다.")).toBeInTheDocument();
  });

  it("경계 입력: 세션이 없는 사용자가 보호 경로(/dashboard)에 접근하면 로그인 화면으로 리다이렉트된다", async () => {
    await act(async () => {
      await router.navigate("/dashboard");
    });
    renderRouter();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "대시보드" })).not.toBeInTheDocument();
  });

  it("거부 입력: 관리자가 아닌 사용자가 관리자 경로(/admin)에 접근하면 관리자 화면 대신 대시보드로 밀려난다", async () => {
    loginFullyOnboarded();

    await act(async () => {
      await router.navigate("/admin");
    });
    renderRouter();

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/dashboard");
    });
    expect(screen.getByRole("heading", { name: "대시보드" })).toBeInTheDocument();
  });
});
