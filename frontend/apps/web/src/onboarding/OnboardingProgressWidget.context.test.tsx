// UX_JOURNEYS.md G-11 (task-25548): 위젯이 어느 라우트/화면에서 렌더돼도
// 동일한 진행률(완료 단계 수/전체)을 보여 주는지를 검증한다.
// OnboardingProgressWidget은 react-query 훅(data/isError)만 읽으므로
// 라우트 로컬 상태에 의존하지 않음 — 이 테스트가 그 불변성을 단언한다.

import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { OnboardingProgressWidget } from "./OnboardingProgressWidget";

// ── mock hooks (shared-hooks) ──────────────────────────────────────

let credentials: { data: unknown; isLoading: boolean; isError: boolean };
let strategies: { data: unknown; isLoading: boolean; isError: boolean };
let deployments: { data: unknown; isLoading: boolean; isError: boolean };

vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => credentials,
  useMyStrategies: () => strategies,
  usePaperDeployments: () => deployments,
}));

// ── mock i18n ──────────────────────────────────────────────────────

vi.mock("react-i18next", async () => {
  const mod = await vi.importActual("react-i18next");
  return {
    ...mod,
    useTranslation: () => ({
      t: (key: string, vars: Record<string, number>) => {
        const ko: Record<string, string> = {
          "onboarding.progressWidget": "온보딩 {{completed}}/{{total}}단계 진행 중",
          "onboarding.progressWidgetLink": "온보딩 체크리스트 보기",
        };
        const en: Record<string, string> = {
          "onboarding.progressWidget": "Onboarding step {{completed}}/{{total}}",
          "onboarding.progressWidgetLink": "View onboarding checklist",
        };
        return (ko[key] ?? en[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, k) => String(vars[k]));
      },
    }),
  };
});

afterEach(() => {
  cleanup();
});

// ── render helper: 라우트에 따라 다른 컴포넌트 트리 안에서 위젯 렌더 ─

function renderWidgetAtRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/onboarding/first-run" element={<OnboardingProgressWidget />} />
        <Route path="/exchanges" element={<OnboardingProgressWidget />} />
        <Route path="/dashboard" element={<OnboardingProgressWidget />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ── tests ───────────────────────────────────────────────────────────

describe("OnboardingProgressWidget.context (G-11, task-25548)", () => {
  // ── 주요 테스트: 서로 다른 라우트에서 동일한 진행률 ──

  it("서로 다른 라우트(/onboarding/first-run, /exchanges, /dashboard)에서 동일한 진행률 텍스트를 보여준다", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    // /onboarding/first-run
    renderWidgetAtRoute("/onboarding/first-run");
    expect(screen.getByText("온보딩 1/3단계 진행 중")).toBeInTheDocument();

    cleanup();

    // /exchanges — 동일한 hooks 데이터 → 같은 진행률
    renderWidgetAtRoute("/exchanges");
    expect(screen.getByText("온보딩 1/3단계 진행 중")).toBeInTheDocument();

    cleanup();

    // /dashboard
    renderWidgetAtRoute("/dashboard");
    expect(screen.getByText("온보딩 1/3단계 진행 중")).toBeInTheDocument();
  });

  // ── negative: 조회 실패 시 fail-closed — 완료로 오인하지 않음 (role=alert) ──

  it("negative: 조회 실패(isError) 시 완료로 오인하지 않고 fail-closed — 미완료 텍스트 유지", () => {
    // exchange 조회 실패, 나머지 완료 — isError이므로 hasExchangeConnection=false
    credentials = { data: [{ id: 1 }], isLoading: false, isError: true };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidgetAtRoute("/exchanges");

    // isError → fail-closed: completedCount=2가 아니라 1 (exchange만 미완료로 처리)
    // isComplete=false → 위젯 표시 → link 존재
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/onboarding/first-run");
  });

  // ── negative: 모든 데이터가 null/undefined/빈 배열이어도 완료로 오인하지 않음 ──

  it("negative: 모든 훅 데이터가 null/undefined/[]이어도 completedCount=0 — 0/3단계 표시", () => {
    credentials = { data: null, isLoading: false, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: [], isLoading: false, isError: false };

    renderWidgetAtRoute("/dashboard");

    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    expect(screen.getByRole("link")).toBeInTheDocument();
  });

  // ── negative: 로딩 중(null 렌더) — 진행률 오인 없음 ──

  it("negative: isLoading=true면 위젯이 null — 진행 중 텍스트가 표시되지 않음", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    renderWidgetAtRoute("/onboarding/first-run");

    // 로딩 중이면 위젯이 null 반환 → 텍스트 없음
    expect(screen.queryByText(/온보딩.*단계 진행 중/)).not.toBeInTheDocument();
  });
});
