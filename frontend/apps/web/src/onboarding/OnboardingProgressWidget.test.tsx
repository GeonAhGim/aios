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

describe("OnboardingProgressWidget(F-4, task-10642)", () => {
  it("아무 단계도 끝나지 않았으면 0/3단계와 체크리스트 링크를 보여준다", () => {
    credentials = { data: [], isLoading: false, isError: false };
    strategies = { data: [], isLoading: false, isError: false };
    deployments = { data: { deployments: [] }, isLoading: false, isError: false };

    renderWidget();

    expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/onboarding/first-run");
  });

  it("로딩 중이면 아무것도 렌더링하지 않는다", () => {
    credentials = { data: undefined, isLoading: true, isError: false };
    strategies = { data: undefined, isLoading: false, isError: false };
    deployments = { data: undefined, isLoading: false, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });

  // negative: 조회 실패(isError)는 fail-closed로 미완료 취급해야 한다 — 거래소 연결
  // 조회가 실패하면 캐시된 data가 남아 있어도 그 단계를 완료로 오인해 위젯을
  // 숨기면(isComplete=true) 안 된다. 나머지 두 단계는 실제로 완료 상태이므로
  // completedCount에는 반영된다.
  it("negative: 거래소 연결 조회가 실패하면(isError) 완료로 오인하지 않고 위젯을 계속 보여준다", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: true };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    renderWidget();

    expect(screen.getByText("온보딩 2/3단계 진행 중")).toBeInTheDocument();
  });

  it("3단계 모두 끝났으면 아무것도 렌더링하지 않는다", () => {
    credentials = { data: [{ id: 1 }], isLoading: false, isError: false };
    strategies = { data: [{ id: "s1" }], isLoading: false, isError: false };
    deployments = { data: { deployments: [{ id: "d1" }] }, isLoading: false, isError: false };

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });
});
