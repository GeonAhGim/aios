import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OnboardingFlowPage } from "./OnboardingFlowPage";

interface QueryResultStub {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
}

function ok(data: unknown): QueryResultStub {
  return { data, isLoading: false, isError: false };
}

let credentialsResult: QueryResultStub = ok([]);
let strategiesResult: QueryResultStub = ok([]);
let deploymentsResult: QueryResultStub = ok({ deployments: [] });

vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => credentialsResult,
  useMyStrategies: () => strategiesResult,
  usePaperDeployments: () => deploymentsResult,
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

function renderPage() {
  render(
    <MemoryRouter>
      <OnboardingFlowPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  credentialsResult = ok([]);
  strategiesResult = ok([]);
  deploymentsResult = ok({ deployments: [] });
});

describe("OnboardingFlowPage 단계 진행", () => {
  it("아무 것도 안 했으면 1단계(거래소 연결) CTA만 노출되고 링크는 /exchanges로 향한다", () => {
    renderPage();

    const cta = screen.getByRole("link", { name: "거래소 연결하기" });
    expect(cta).toHaveAttribute("href", "/exchanges");
    expect(screen.queryByRole("link", { name: "전략 만들기" })).not.toBeInTheDocument();
  });

  it("거래소 연결 후에는 2단계(전략 생성) CTA가 노출되고 1단계는 완료 배지로 바뀐다", () => {
    credentialsResult = ok([{ id: 1, exchange: "bitget" }]);
    renderPage();

    expect(screen.getByRole("link", { name: "전략 만들기" })).toHaveAttribute(
      "href",
      "/strategy-builder",
    );
    expect(screen.getAllByText("완료").length).toBeGreaterThanOrEqual(1);
  });

  it("세 단계 모두 끝나면 완주 화면을 보여주고 대시보드로 가는 링크를 제공한다(첫 실행 완주)", () => {
    credentialsResult = ok([{ id: 1, exchange: "bitget" }]);
    strategiesResult = ok([{ id: "s1" }]);
    deploymentsResult = ok({ deployments: [{ id: "d1", state: "RUNNING" }] });
    renderPage();

    expect(screen.getByRole("link", { name: "대시보드로 이동" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("로딩 중에는 단계 카드 대신 로딩 표시를 보여준다", () => {
    credentialsResult = { data: undefined, isLoading: true, isError: false };
    renderPage();

    expect(screen.queryByRole("link", { name: "거래소 연결하기" })).not.toBeInTheDocument();
  });

  it("negative: 전략 조회가 isError=true면 캐시된 비어있지 않은 data가 있어도 2단계를 완료로 인정하지 않는다(fail-closed)", () => {
    credentialsResult = ok([{ id: 1, exchange: "bitget" }]);
    strategiesResult = { data: [{ id: "stale-cached" }], isLoading: false, isError: true };
    renderPage();

    expect(screen.getByRole("link", { name: "전략 만들기" })).toBeInTheDocument();
  });
});
