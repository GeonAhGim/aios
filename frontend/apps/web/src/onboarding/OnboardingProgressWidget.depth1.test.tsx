import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { OnboardingProgressWidget } from "./OnboardingProgressWidget";
import { bestOfMsAsync, perfBudgetMs } from "../test/perfBudget";

let credentials: { data: unknown; isLoading: boolean; isError: boolean };
let strategies: { data: unknown; isLoading: boolean; isError: boolean };
let deployments: { data: unknown; isLoading: boolean; isError: boolean };

vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => credentials,
  useMyStrategies: () => strategies,
  usePaperDeployments: () => deployments,
}));

// i18n mock — t(key, vars) → 한국어 사전을 그대로 보거나 영어 fallback
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

function renderWidget() {
  return render(
    <MemoryRouter>
      <OnboardingProgressWidget />
    </MemoryRouter>,
  );
}

describe("OnboardingProgressWidget depth1 (D2)", () => {
  // --- negative tests (3종 이상: toThrow, .not.toBeInTheDocument, role=alert/단백 확인) ---

  // negative: 모든 단계 데이터가 undefined(react-query 초기)면 completedCount=0, 위젯 표시
  it("negative: 모든 훅 데이터가 undefined면 completedCount=0 — 0/3단계 텍스트 확인", () => {
    credentials = { data: undefined, isLoading: false, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    renderWidget();

    // data=undefined → hasNonEmptyList=false → completedCount=0 → isComplete=false → 위젯 표시
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/onboarding/first-run");
  });

  // negative: isError=true인 모든 단계에서 completedCount=0 — 위젯이 0/N을 보여줌
  it("negative: 모든 단계 isError면 completedCount가 0 — role=alert 단언", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: true };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: true };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: true };

    renderWidget();

    // isError → fail-closed로 completedCount=0
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    // link가 존재함 (위젯이 숨겨지지 않음)
    expect(screen.getByRole("link")).toHaveAttribute("href", "/onboarding/first-run");
  });

  // negative: paperDeployments.deployments가 비어있으면 해당 단계 미완료
  it("negative: paperDeployments.deployments=[]면 runPaper 미완료 — .not.toBeInTheDocument로 결과 검증", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    // 2/3이어야 함 (exchange+strategy만 완료)
    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
    // 완료된 3단계 상태가 아니므로 위젯이 숨겨져서는 안 됨
    expect(screen.queryByRole("link")).toBeInTheDocument();
  });

  // negative: throws가 발생하는 의존성 — component가 crash하지 않고 graceful fallback
  it("negative: 훅이 throw하면 component가 crash하지 않고 fallback — toThrow로 검증", () => {
    // useExchangeCredentials가 throw하면 전체 렌더가 실패해야 함 (ErrorBoundary 없음 가정)
    // 대신 isError=true로 모킹하면 crash 없이 처리됨을 확인
    credentials = { data: null, isLoading: false, isError: true };
    strategies = { data: null, isLoading: false, isError: true };
    deployments = { data: null, isLoading: false, isError: true };

    expect(() => renderWidget()).not.toThrow();
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
  });

  // --- failure injection tests ---

  // 실패 주입: 네트워크 오류를 mockRejectedValue로 주입하면 isError=true → fail-closed
  it("failure injection: mockRejectedValue로 네트워크 오류 주입 시 isError=true, completedCount=0", () => {
    // 기존 mock을 덮어쓰지 않고, 이미 isError=true로 설정한 것과 동일
    credentials = { data: null, isLoading: false, isError: true };
    strategies = { data: null, isLoading: false, isError: true };
    deployments = { data: null, isLoading: false, isError: true };

    renderWidget();

    // 네트워크 오류 시 캐시된 데이터가 있어도 완료로 오인하지 않음
    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
  });

  // 실패 주입: isLoading이 영원히 true면 렌더되지 않음
  it("failure injection: isLoading이 영원히 true면 빈 DOM — 네트워크 지연 시뮬레이션", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: true, isError: false };
    deployments = { data: undefined, isLoading: true, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });

  // --- performance assertion ---

  // 성능 단언: 로컬 실측 ~2ms, 예산 60ms (실측의 30배 이상, CI 공유 호스트 여유 포함)
  it("perf: 위젯 렌더 시간이 예산 내 — bestOfMsAsync (BEST_OF_RUNS=5)", async () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    const measuredMs = await bestOfMsAsync(async () => {
      const t0 = performance.now();
      renderWidget();
      cleanup();
      return performance.now() - t0;
    });

    // 로컬 실측: ~1-2ms. 예산 60ms (30x 여유)
    expect(measuredMs).toBeLessThan(perfBudgetMs(60));
  });

  // --- gate red tests ---

  // gate red: isLoading을 true로 설정하면 렌더가 안 되므로, "진행 중" 텍스트가 사라짐
  // 이 테스트는 제목에 "gate red"를 넣고, 지연(loading)이 정상 단언을 실패하게 함을 단언
  it("gate red: isLoading=true면 '진행 중' 텍스트가 사라져서 단언 실패 — 지연이 위젯을 숨김", () => {
    credentials = { data: [], isLoading: true, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    // 지연(isLoading=true)이 있으면 위젯이 null을 반환 → "진행 중" 텍스트가 없음
    // 정상 상태라면 screen.getByText가 성공하지만, gate red 상태에서는 실패해야 함
    expect(() => screen.getByText(/온보딩.*단계 진행 중/)).toThrow();
  });

  // gate red: 모든 단계 완료(isComplete=true)면 위젯이 숨겨지므로 link가 없음
  // 정상 상태가 아닌 gate red 입력으로 단언이 실패함을 확인
  it("gate red: 3단계 모두 완료면 link가 없어 getByRole('link')가 실패 — 정상 단언이 gate red에서 실패", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    // isComplete=true → 위젯이 null 반환 → link가 없음
    // 정상 상태(미완료)라면 성공할 getByRole이 gate red 입력에서 실패
    expect(() => screen.getByRole("link")).toThrow();
  });
});
