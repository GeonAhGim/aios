import "./i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@aios/shared-hooks/src/useAuthStore";

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
//
// task-8042: 이 파일만 유일하게 async factory(`importOriginal`) 목과 top-level
// `await import("./router")`(46개 페이지 정적 import 그래프)를 같이 쓴다. CI에서
// 딱 한 번(run 36219219761) 실제 useNotificationHistory가 QueryClientProvider
// 없이 호출돼 "No QueryClient set"으로 죽었는데 — importOriginal()의 비동기
// 완료 이전에 router.tsx가 끌어오는 46개 페이지 모듈 그래프가 동시에
// "@aios/shared-hooks" 목을 요청하면서 완성 전 상태가 캐시된 것으로 보인다
// (로컬 재현 2회는 모두 통과 — CPU 경합·커버리지 계측 타이밍에서만 드러나는
// task-1968류 레이스). importOriginal 없이 동기 팩토리로 바꿔 그 레이스 자체를
// 제거한다 — useAuthStore는 실제 싱글턴 모듈을 배럴을 거치지 않고 직접 가져와
// 목 객체에 그대로 얹으므로 앱 코드가 배럴로 참조하는 것과 동일 인스턴스다.
vi.mock("@aios/shared-hooks", () => ({
  useAuthStore,
  useMe: () => meResult,
  useRiskProfile: () => riskResult,
  usePortfolio: () => ({ data: undefined, isLoading: false }),
  useExecutions: () => ({ data: undefined, isLoading: false }),
  // task-7776 G-2 최근 알림 위젯 — DashboardPage가 새로 쓰는 훅. 목이 없으면 실제
  // useQuery가 QueryClientProvider 없는 이 렌더에서 "No QueryClient set"으로 죽는다.
  useNotificationHistory: () => ({ data: undefined, isLoading: false, isError: false, error: null }),
  useLogout: () => vi.fn(),
  useLogin: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const { router } = await import("./router");

function renderRouter() {
  return render(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
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
