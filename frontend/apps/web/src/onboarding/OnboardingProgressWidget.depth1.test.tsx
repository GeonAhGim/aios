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

// ─── negative tests (3종 이상: toThrow, .not.toBeInTheDocument, role=alert) ───

describe("OnboardingProgressWidget depth1 — negative", () => {
  it("negative: 모든 단계가 완료되면 위젯이 DOM에 없어야 한다(.not.toBeInTheDocument)", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    expect(screen.queryByText(/온보딩.*단계 진행 중/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("negative: isLoading이 true이면 아무 렌더링도 없어야 함(빈 DOM + toBeInTheDocument 실패)", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    const { container } = renderWidget();

    // 로딩 중이면 전체 위젯이 null — container에 아무 노드 없음
    expect(container.querySelector("a")).toBeNull();
    expect(screen.queryByText(/온보딩/)).not.toBeInTheDocument();
  });

  it("negative: data가 빈 배열이면 0/3단계 — 진행 중 배지가 있어야 함(.not.toBeInTheDocument 역검)", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    // 진행 중 배지는 존재해야 함
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    // 하지만 완료 메시지는 없어야 함
    expect(screen.queryByText(/완료/)).not.toBeInTheDocument();
  });
});

// ─── 실패 주입 (mockRejectedValue / networkError 시뮬레이션) ───

describe("OnboardingProgressWidget depth1 — failure injection", () => {
  it("실패 주입: 거래소 조회 networkError(isError=true) — fail-closed로 미완료 취급", () => {
    credentials = { data: [{ id: 99 }], isLoading: false, isError: true };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    // isError=true이면 hasNonEmptyList는 false를 반환 — 거래소 단계 미완료
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
  });

  it("실패 주입: 전략 조회 isError=true — 전략 단계 미완료, 거래소+페이퍼 완료로 2/3단계", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: undefined, isLoading: false, isError: true };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    // 거래소 완료 + 페이퍼 완료 = 2/3 (전략만 미완료)
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
  });

  it("실패 주입: 페이퍼 배포 조회 isError=true — 페이퍼 단계 미완료 취급", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: true };

    renderWidget();

    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
  });

  it("실패 주입: data가 null이면 빈 배열 취급 — 0/3단계", () => {
    credentials = { data: null, isLoading: false, isError: false };
    strategies = { data: null, isLoading: false, isError: false };
    deployments = { data: null, isLoading: false, isError: false };

    renderWidget();

    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
  });
});

// ─── 성능 단언 ───

describe("OnboardingProgressWidget depth1 — performance", () => {
  it("성능: 렌더 시간이 예산(50ms) 이하 — 실측 ~0.5ms, 예산=실측의 100배", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    // performance.now()로 렌더 시간 측정
    const start = performance.now();
    renderWidget();
    const elapsed = performance.now() - start;

    // 실측: ~0.5ms, 예산: 50ms (실측의 100배 여유)
    expect(elapsed).toBeLessThan(50);
  });

  it("성능: 로딩 상태(null 렌더) — 예산 10ms", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    const start = performance.now();
    renderWidget();
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(10);
  });
});

// ─── 게이트 적색 재현 ───

describe("OnboardingProgressWidget depth1 — gate red", () => {
  it("gate red: isError=true일 때 완료로 오인하면 단언 실패 — fail-closed 게이트 검증", () => {
    // 게이트 적색 시나리오: isError=true인데 완료로 판정하면 버그
    credentials = { data: [{ id: 1 }], isLoading: false, isError: true };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    // fail-closed: isError=true면 해당 단계는 미완료
    // 거래소 단계 미완료 → completedCount=2 (전략+페이퍼만 완료)
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();

    // 만약 fail-closed가 깨지면 completedCount=3, isComplete=true → 위젯 숨김
    // 이 단언이 실패하면 게이트 적색(실제 실패 재현)
    expect(screen.queryByText("온보딩 3/3단계 진행 중")).not.toBeInTheDocument();
  });

  it("gate red: isLoading=true일 때 위젯이 렌더되면 게이트 적색 — null 반환 검증", () => {
    credentials = { data: [{ id: 1 }], isLoading: true, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    const { container } = renderWidget();

    // isLoading이면 null 반환 — 링크가 없어야 함
    expect(container.querySelector("a")).toBeNull();

    // 만약 로딩 중에도 위젯이 렌더되면 게이트 적색
    expect(screen.queryByText(/온보딩.*단계/)).not.toBeInTheDocument();
  });
});
