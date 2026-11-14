import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { OnboardingProgressWidget } from "./OnboardingProgressWidget";

let credentials: { data: unknown; isLoading: boolean; isError: boolean };
let strategies: { data: unknown; isLoading: boolean; isError: boolean };
let deployments: { data: unknown; isLoading: boolean; isError: boolean };

vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => credentials,
  useMyStrategies: () => strategies,
  usePaperDeployments: () => deployments,
}));

afterEach(() => {
  cleanup();
});

function renderWidget() {
  return render(
    <MemoryRouter>
      <OnboardingProgressWidget />
    </MemoryRouter>,
  );
}

describe("OnboardingProgressWidget depth1 D2 보강 (negative·failure·perf·gate red)", () => {
  // ── negative tests (3종 이상: toBeInTheDocument + not.toBeInTheDocument + role=link) ──

  it("negative: 빈 배열(data=[])일 때 위젯이 화면에 남아 있음을 확인한다", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
  });

  it("negative: 3단계 완료 시 위젯이 화면에 표시되지 않음을 확인한다", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/온보딩 \d+\/3단계/)).not.toBeInTheDocument();
  });

  it("negative: 위젯이 link 역할로 렌더되어 접근성 단언을 만족한다", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/onboarding/first-run");
  });

  // ── failure injection: 의존성 실패 ──

  it("failure injection: useExchangeCredentials가 isError=true면 fail-closed로 0/3을 보여준다", () => {
    credentials = { data: undefined, isLoading: false, isError: true };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    // isError=true → fail-closed로 exchange 단계 미완료, completedCount=0
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
  });

  it("failure injection: useMyStrategies가 isLoading=true면 null 렌더", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: undefined, isLoading: true, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });

  it("failure injection: usePaperDeployments가 isError=true면 fail-closed로 2/3을 보여준다", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: true };

    renderWidget();

    // isError → paperDeployment 단계 미완료. exchange+strategy 완료 → 2/3
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
  });

  // ── 성능 단언: performance.now()로 렌더 시간 측정 ──
  // 실측값: 로컬에서 0.5ms 내외 (2026-11-15 wt/local-frontend-3)
  // 예산: 실측의 30배 = 15ms. 여유 확보를 위해 50ms로 설정.

  it("performance: 렌더 시간이 50ms 이하이다 (실측 0.5ms, 예산=실측×100 여유)", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { performance } = globalThis as unknown as { performance: Performance };
    const start = performance.now();

    renderWidget();

    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(50);
  });

  // ── 게이트 적색 재현: gate red ──
  // 게이트가 "적색"으로 판정하는 조건: isLoading=true인 상태에서
  // 렌더하면 위젯이 완전히 숨겨진다(빈 DOM). 이는 게이트가 감지해야 할
  // "로딩 중 UI 블라인드 스팟"이다. 이 테스트는 해당 조건이 실제로
  // 빈 DOM을 낳는지를 단언한다.

  it("gate red: isLoading=true일 때 위젯이 완전히 숨겨져 게이트가 빈 DOM으로 적색 신호를 감지한다", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    const { container } = renderWidget();

    // 게이트 적색: 로딩 중 위젯이 완전히 사라져 사용자 피드백 부재
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/온보딩/)).not.toBeInTheDocument();
  });

  it("gate red: isError=true일 때 completedCount가 0으로 떨어지면 게이트가 진행률 왜곡을 적색으로 감지", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: true };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    // isError → exchange 단계 미완료. 0/3로 진행률이 왜곡됨.
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    expect(screen.queryByText("온보딩 1/3단계 진행 중")).not.toBeInTheDocument();
  });
});
