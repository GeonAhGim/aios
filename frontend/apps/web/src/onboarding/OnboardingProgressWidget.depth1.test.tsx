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

function setHealthy(counts: { exchange: number; strategy: number; paper: number }) {
  credentials = { data: Array.from({ length: counts.exchange }, (_, i) => ({ id: i })), isLoading: false, isError: false };
  strategies = { data: Array.from({ length: counts.strategy }, (_, i) => ({ id: `s${i}` })), isLoading: false, isError: false };
  deployments = {
    data: { deployments: Array.from({ length: counts.paper }, (_, i) => ({ id: `d${i}` })) },
    isLoading: false,
    isError: false,
  };
}

// 진행 단언 한 벌. 게이트 적색 테스트가 같은 함수를 망가진 입력에 다시 써서
// 실제로 실패하는지 확인한다.
function expectProgressText(text: string) {
  expect(screen.getByText(text)).toBeInTheDocument();
}

describe("OnboardingProgressWidget depth1 D2 보강 (negative·failure·perf·gate red)", () => {
  // ── negative: toBeInTheDocument 반대 단언·toThrow 단언 ──

  it("negative: 3단계 모두 완료되면 위젯 링크가 화면에 남지 않는다", () => {
    setHealthy({ exchange: 1, strategy: 1, paper: 1 });

    renderWidget();

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/온보딩 \d+\/3단계/)).not.toBeInTheDocument();
  });

  it("negative: 거래소 조회가 실패하면 완료 단계로 세지 않아 1/3 문구가 나타나지 않는다", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });
    credentials = { ...credentials, isError: true };

    renderWidget();

    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    expect(screen.queryByText("온보딩 1/3단계 진행 중")).not.toBeInTheDocument();
  });

  it("negative: 0/3 단언은 실제 1/3 렌더 결과에 대해 실패한다(toThrow)", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });

    renderWidget();

    expect(() => expectProgressText("온보딩 0/3단계 진행 중")).toThrow();
    expectProgressText("온보딩 1/3단계 진행 중");
  });

  // ── failure injection: 의존성 실패(isError) 주입 ──

  it("failure injection: useExchangeCredentials가 isError=true면 fail-closed로 0/3을 보여준다", () => {
    setHealthy({ exchange: 0, strategy: 0, paper: 0 });
    credentials = { data: undefined, isLoading: false, isError: true };

    renderWidget();

    expectProgressText("온보딩 0/3단계 진행 중");
  });

  it("failure injection: useMyStrategies가 로딩 지연 중이면 아무것도 렌더링하지 않는다", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });
    strategies = { data: undefined, isLoading: true, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });

  it("failure injection: usePaperDeployments가 isError=true면 fail-closed로 2/3을 보여준다", () => {
    setHealthy({ exchange: 1, strategy: 1, paper: 1 });
    deployments = { data: { deployments: [{ id: "d0" }] }, isLoading: false, isError: true };

    renderWidget();

    expectProgressText("온보딩 2/3단계 진행 중");
  });

  // ── performance: performance.now()로 렌더 시간 측정 ──
  // 실측(로컬 jsdom, 2026-11-15, 20회 반복): 웜 중앙값 약 0.6ms, 콜드 첫 렌더 약 15ms.
  // 예산 50ms = 웜 중앙값의 80배, 실측 최대(콜드)의 3배 이상 여유. 예산 상향 금지.

  it("performance: 3단계 렌더 시간이 50ms 예산 이내다", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });

    const start = performance.now();
    renderWidget();
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(50);
  });

  // ── gate red: 입력·지연을 망가뜨리면 같은 단언이 실제로 실패함을 확인 ──

  it("gate red: 거래소 입력을 빼면 정상 1/3 단언이 실패한다", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });
    renderWidget();
    expectProgressText("온보딩 1/3단계 진행 중");
    cleanup();

    setHealthy({ exchange: 0, strategy: 0, paper: 0 });
    renderWidget();

    expect(() => expectProgressText("온보딩 1/3단계 진행 중")).toThrow();
    expectProgressText("온보딩 0/3단계 진행 중");
  });

  it("gate red: 로딩 지연을 주입하면 같은 진행 단언이 실패한다(위젯 숨김)", () => {
    setHealthy({ exchange: 1, strategy: 0, paper: 0 });
    credentials = { data: undefined, isLoading: true, isError: false };

    renderWidget();

    expect(() => expectProgressText("온보딩 1/3단계 진행 중")).toThrow();
    expect(screen.queryByText(/온보딩/)).not.toBeInTheDocument();
  });
});
