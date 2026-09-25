import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, WhatIfRouteNotImplementedError, type WhatIfClient } from "@aios/api-client";
import type { WhatIfRebalancePlanResponse } from "@aios/shared-types";
import { RebalancePage, type RebalancePageProps } from "./RebalancePage";
import { perfBudgetMs } from "../../test/perfBudget";
// i18n/index.ts(task-2685)의 initI18n()이 모듈 로드 시 1회 부수효과로 실행된다 —
// ScreenerPage.test.tsx와 동일 사유(check_i18n_literals.mjs 게이트).
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function planResponse(overrides: Partial<WhatIfRebalancePlanResponse> = {}): WhatIfRebalancePlanResponse {
  return {
    trades: [
      {
        symbol: "BTC-USDT",
        side: "BUY",
        quantity: "0.1",
        price: "50000",
        notional: "5000",
        deltaWeightPct: "5",
      },
    ],
    turnoverPct: "2.5",
    estCost: "12.5",
    skipped: [],
    ...overrides,
  };
}

function stubClient(overrides: Partial<WhatIfClient> = {}): WhatIfClient {
  return {
    previewOrder: vi.fn(),
    planRebalance: vi.fn().mockResolvedValue(planResponse()),
    ...overrides,
  };
}

function renderPage(props: RebalancePageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/rebalance"]}>
        <Routes>
          <Route path="/rebalance" element={<RebalancePage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillTarget(index: number, symbol: string, weight: string) {
  fireEvent.change(screen.getByTestId(`rebalance-target-${index}-symbol`), { target: { value: symbol } });
  fireEvent.change(screen.getByTestId(`rebalance-target-${index}-weight`), { target: { value: weight } });
}

function run() {
  fireEvent.click(screen.getByTestId("rebalance-run"));
}

describe("RebalancePage", () => {
  it("실행 전에는 안내만 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    expect(screen.getByText("목표 비중을 구성하고 계획을 생성하세요.")).toBeInTheDocument();
    expect(client.planRebalance).not.toHaveBeenCalled();
  });

  it("negative 1/4: 목표를 전부 지우고 실행하면 '목표 비중을 하나 이상 추가하세요.' 오류를 보여준다", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fireEvent.click(screen.getByTestId("rebalance-target-0-remove"));
    run();

    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent("목표 비중을 하나 이상 추가하세요.");
    expect(client.planRebalance).not.toHaveBeenCalled();
  });

  it("negative 2/4: 심볼이 비어 있으면 1번째 목표를 지목하는 검증 오류를 보여준다", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fireEvent.change(screen.getByTestId("rebalance-target-0-weight"), { target: { value: "30" } });
    run();

    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent("1번째 목표의 종목을 입력하세요.");
    expect(client.planRebalance).not.toHaveBeenCalled();
  });

  it("negative 3/4: 비중이 0~100 범위를 벗어나면(100 초과) 검증 오류를 보여준다", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "150");
    run();

    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent(
      "1번째 목표의 비중은 0~100 사이 숫자여야 합니다.",
    );
    expect(client.planRebalance).not.toHaveBeenCalled();
  });

  it("negative 4/4: 심볼이 중복되면(조용한 덮어쓰기 방지) 검증 오류를 보여준다", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    fireEvent.click(screen.getByTestId("rebalance-add-target"));
    fillTarget(1, "BTC-USDT", "20");
    run();

    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent("2번째 종목이 중복되었습니다.");
    expect(client.planRebalance).not.toHaveBeenCalled();
  });

  it("거래가 없으면(전부 밴드 이내) 빈 상태를 보여준다(크래시 없음)", async () => {
    const client = stubClient({
      planRebalance: vi.fn().mockResolvedValue(planResponse({ trades: [], skipped: ["BTC-USDT:REBALANCE_BAND"] })),
    });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("생성된 거래가 없습니다.")).toBeInTheDocument();
    expect(screen.getByTestId("rebalance-skipped-list")).toHaveTextContent("BTC-USDT:REBALANCE_BAND");
  });

  it("유효한 목표로 실행하면 거래 목록과 회전율·예상 비용을 보여준다", async () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByTestId("rebalance-trade-BTC-USDT")).toHaveTextContent("BTC-USDT · BUY · 0.1");
    expect(screen.getByTestId("rebalance-turnover")).toHaveTextContent("회전율 2.5%");
    expect(screen.getByTestId("rebalance-est-cost")).toHaveTextContent("예상 비용 12.5");
  });

  it("라우트가 아직 없으면(WhatIfRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      planRebalance: vi.fn().mockRejectedValue(new WhatIfRouteNotImplementedError("whatif.rebalancePlan")),
    });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("리밸런싱 계획 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 실패주입: ScreenerPage.test.tsx(DEPTH_UX task-2692) 관용과 동일.
  it("실패주입 1/3: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({ planRebalance: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 2/3: buildApiError로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-rebalance-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const planRebalance = vi.fn().mockRejectedValue(realError);
    renderPage({ whatIfClient: stubClient({ planRebalance }) });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("지원코드: trace-rebalance-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(planRebalance).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/3: buildApiError로 만든 500(INTERNAL_ERROR)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      500,
      {
        error_code: "INTERNAL_ERROR",
        message: "일시적인 오류가 발생했습니다.",
        trace_id: "trace-rebalance-500",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    renderPage({ whatIfClient: stubClient({ planRebalance: vi.fn().mockRejectedValue(realError) }) });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("지원코드: trace-rebalance-500")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("거래 행의 '영향 미리보기'를 누르면 WhatIfPanel이 해당 거래를 초기값으로 렌더해 자동 조회한다", async () => {
    const previewOrder = vi.fn().mockResolvedValue({
      exposureDeltaPct: "0.8",
      concentrationDeltaPct: "0.1",
      varDelta: "10",
      limitHeadroomDelta: "-5",
    });
    const client = stubClient({ previewOrder });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    fireEvent.click(await screen.findByTestId("rebalance-trade-BTC-USDT-preview"));

    expect(await screen.findByTestId("whatif-impact-result")).toBeInTheDocument();
    await waitFor(() => expect(previewOrder).toHaveBeenCalledWith({ symbol: "BTC-USDT", side: "BUY", quantity: "0.1" }));
  });

  // 성능: 200건의 거래도 예산 시간 안에 렌더해야 한다(ScreenerPage.test.tsx task-2692
  // 관용과 동일하게 수치 예산으로 못박는다).
  it("성능: 200건의 거래도 예산 시간 안에 렌더한다", async () => {
    const trades = Array.from({ length: 200 }, (_, i) => ({
      symbol: `SYM-${i}`,
      side: "BUY" as const,
      quantity: "1",
      price: "100",
      notional: "100",
      deltaWeightPct: "1",
    }));
    const client = stubClient({ planRebalance: vi.fn().mockResolvedValue(planResponse({ trades })) });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");

    const startedAt = performance.now();
    run();
    await screen.findByTestId("rebalance-trade-SYM-199");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="rebalance-trade-"]')).toHaveLength(400);
  });

  // 게이트적색: 아래 2개는 RebalancePage.tsx의 특정 분기를 정확히 겨눈다 — 그 분기를
  // 되돌리면 해당 테스트만 적색이 된다.
  it("게이트적색 1/2: 실행 요청 본문에는 목표 행의 내부 id가 섞여 들어가지 않는다(handleRun의 id 제거 분기)", async () => {
    const planRebalance = vi.fn().mockResolvedValue(planResponse());
    renderPage({ whatIfClient: stubClient({ planRebalance }) });

    fillTarget(0, "BTC-USDT", "30");
    run();

    await waitFor(() => expect(planRebalance).toHaveBeenCalledTimes(1));
    const sentTargets = planRebalance.mock.calls[0][0];
    expect(sentTargets[0]).toEqual({ symbol: "BTC-USDT", targetWeightPct: "30" });
    expect(sentTargets[0].id).toBeUndefined();
  });

  it("게이트적색 2/2: 건너뜀 목록은 skipped가 비어 있으면 렌더되지 않는다(빈 배열 분기)", async () => {
    const client = stubClient({ planRebalance: vi.fn().mockResolvedValue(planResponse({ skipped: [] })) });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    await screen.findByTestId("rebalance-trade-BTC-USDT");
    expect(screen.queryByTestId("rebalance-skipped-list")).not.toBeInTheDocument();
  });
});
