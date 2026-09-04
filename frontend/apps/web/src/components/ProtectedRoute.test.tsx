import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, useAuthStore } from "@aios/shared-hooks";
import { ProtectedRoute } from "./ProtectedRoute";

// task-1197: §3.4 세션 fail-closed 회귀. useMe/useRiskProfile은 통제 가능한 목으로
// 바꾸되(브랜치 판정을 결정론적으로 흔들기 위함), useAuthStore·apiClient는 그대로
// 둔다(importOriginal) — DoD(3)의 "세션 전역 401 이벤트" 시나리오는 useAuthStore.ts가
// 앱 부트스트랩 시점에 실제로 등록하는 configureUnauthorizedHandler 배선(task-354)을
// 그대로 타야 하고, 그걸 흉내 낸 목 콜백을 대신 부르면 실제 배선이 끊겨도 초록으로
// 남는 동어반복이 된다(PortfolioPage.errors.test.tsx 선례와 같은 이유).
let meResult: { data: { mfaEnabled?: boolean } | undefined; isLoading: boolean } = {
  data: undefined,
  isLoading: false,
};
let riskResult: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false };

vi.mock("@aios/shared-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/shared-hooks")>();
  return {
    ...actual,
    useMe: () => meResult,
    useRiskProfile: () => riskResult,
  };
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function authErrorBody(errorCode: string) {
  return { error_code: errorCode, message: "세션 오류", details: {}, trace_id: "t1", retry_after_seconds: null };
}

function renderProtected(initialPath = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <div>PROTECTED_CONTENT</div>
            </ProtectedRoute>
          }
        />
        <Route path="/login" element={<div>LOGIN_PAGE</div>} />
        <Route path="/onboarding/mfa-setup" element={<div>MFA_SETUP_PAGE</div>} />
        <Route path="/onboarding/risk-assessment" element={<div>RISK_ASSESSMENT_PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // 누수된 토큰·401 알림 가드가 다음 테스트를 초록으로 만드는 것이 이 파일의
  // 가장 위험한 오탐이다 — 매 테스트 뒤 실제 스토어를 로그아웃 상태로 되돌린다
  // (logout()이 아니라 setToken이 resetUnauthorizedGuard도 호출하지만, 다음
  // 테스트는 토큰 없음이 기본값이어야 하므로 logout으로 정리한다).
  act(() => {
    useAuthStore.getState().logout();
  });
  meResult = { data: undefined, isLoading: false };
  riskResult = { data: undefined, isLoading: false };
});

describe("ProtectedRoute — §3.4 세션 fail-closed", () => {
  it("비인증(토큰 없음) 상태에서는 보호 자식을 렌더하지 않고 로그인으로 보낸다", () => {
    renderProtected();

    expect(screen.getByText("LOGIN_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
  });

  it("세션 조회 중(로딩)에는 보호 자식이 렌더되지 않는다 — 로딩 화면만 보인다", () => {
    act(() => useAuthStore.getState().setToken("token-loading"));
    meResult = { data: undefined, isLoading: true };
    riskResult = { data: undefined, isLoading: true };

    renderProtected();

    expect(screen.getByText("로딩 중...")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
    expect(screen.queryByText("LOGIN_PAGE")).not.toBeInTheDocument();
  });

  it("토큰은 있으나 세션 조회가 실패(만료 토큰 등으로 me=undefined)하면 보호 자식을 렌더하지 않고 로그인으로 보낸다", () => {
    act(() => useAuthStore.getState().setToken("token-expired"));
    meResult = { data: undefined, isLoading: false };
    riskResult = { data: undefined, isLoading: false };

    renderProtected();

    expect(screen.getByText("LOGIN_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
  });

  it("MFA 미설정 사용자는 온보딩으로 강제 이동하고 보호 자식은 렌더되지 않는다", () => {
    act(() => useAuthStore.getState().setToken("token-nomfa"));
    meResult = { data: { mfaEnabled: false }, isLoading: false };
    riskResult = { data: undefined, isLoading: false };

    renderProtected();

    expect(screen.getByText("MFA_SETUP_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
  });

  it("적합성평가 미완료 사용자는 온보딩으로 강제 이동하고 보호 자식은 렌더되지 않는다", () => {
    act(() => useAuthStore.getState().setToken("token-norisk"));
    meResult = { data: { mfaEnabled: true }, isLoading: false };
    riskResult = { data: undefined, isLoading: false };

    renderProtected();

    expect(screen.getByText("RISK_ASSESSMENT_PAGE")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
  });

  it("정상 세션(토큰·MFA·적합성평가 모두 충족)이면 보호 자식을 렌더한다", () => {
    act(() => useAuthStore.getState().setToken("token-ok"));
    meResult = { data: { mfaEnabled: true }, isLoading: false };
    riskResult = { data: { riskLevel: "moderate" }, isLoading: false };

    renderProtected();

    expect(screen.getByText("PROTECTED_CONTENT")).toBeInTheDocument();
  });

  it("(3) 세션 전역 401 AUTH_* 이벤트가 configureUnauthorizedHandler 실배선 경로로 발생하면 보호 화면을 즉시 닫고 로그인으로 보낸다", async () => {
    act(() => useAuthStore.getState().setToken("token-live"));
    meResult = { data: { mfaEnabled: true }, isLoading: false };
    riskResult = { data: { riskLevel: "moderate" }, isLoading: false };

    renderProtected();
    expect(screen.getByText("PROTECTED_CONTENT")).toBeInTheDocument();

    // 실제 apiClient(useAuthStore.ts가 부트스트랩 시점에 configureUnauthorizedHandler로
    // 구독한 바로 그 http.ts 경로)에 401 AUTH_TOKEN_INVALID를 태운다 — refresh 대상이
    // 아닌 코드라 refreshAccessToken() 없이 곧장 notifyUnauthorized로 간다(task-386).
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, authErrorBody("AUTH_TOKEN_INVALID")));
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      await expect(apiClient.getMe()).rejects.toThrow();
    });

    expect(useAuthStore.getState().token).toBeNull();
    await waitFor(() => expect(screen.getByText("LOGIN_PAGE")).toBeInTheDocument());
    expect(screen.queryByText("PROTECTED_CONTENT")).not.toBeInTheDocument();
  });
});
