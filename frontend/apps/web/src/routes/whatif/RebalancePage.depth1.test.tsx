import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, type WhatIfClient } from "@aios/api-client";
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

describe("RebalancePage depth1 — D2 신호 보강", () => {
  // ── negative tests (3종: toThrow, not.toBeInTheDocument, role=alert) ──

  it("negative(depth1-1): 빈 심볼로 실행하면 throw 없이 role=alert 검증 메시지가 렌더되고 API 호출 안 함", () => {
    const planRebalance = vi.fn();
    renderPage({ whatIfClient: stubClient({ planRebalance }) });

    fillTarget(0, "", "30");
    run();

    expect(planRebalance).not.toHaveBeenCalled();
    expect(screen.getByTestId("rebalance-validation-alert")).toBeInTheDocument();
    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent("종목을 입력하세요.");
  });

  it("negative(depth1-2): 비중 150 입력 시 not.toBeInTheDocument으로 결과 영역이 안 뜨고 alert가 나타남", async () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "150");
    run();

    expect(client.planRebalance).not.toHaveBeenCalled();
    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent(
      "1번째 목표의 비중은 0~100 사이 숫자여야 합니다.",
    );
    expect(screen.queryByTestId("rebalance-impact-result")).not.toBeInTheDocument();
  });

  it("negative(depth1-3): 중복 심볼 입력 시 not.toBeInTheDocument으로 거래 결과가 안 보이고 중복 오류 alert", async () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    fireEvent.click(screen.getByTestId("rebalance-add-target"));
    fillTarget(1, "BTC-USDT", "20");
    run();

    expect(client.planRebalance).not.toHaveBeenCalled();
    expect(screen.getByTestId("rebalance-validation-alert")).toHaveTextContent("2번째 종목이 중복되었습니다.");
    expect(screen.queryByTestId("rebalance-trade-BTC-USDT")).not.toBeInTheDocument();
  });

  // ── failure injection ──

  it("실패주입(depth1-1): planRebalance가 mockRejectedValue하면 크래시 없이 오류 배너 렌더", async () => {
    const client = stubClient({
      planRebalance: vi.fn().mockRejectedValue(new TypeError("Network failure")),
    });
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");
    run();

    expect(await screen.findByText("Network failure")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입(depth1-2): 429 응답에서 재시도 버튼 클릭 시 planRebalance가 2회 호출됨", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "Too many requests",
        trace_id: "trace-depth1-429",
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

    expect(await screen.findByText("지원코드: trace-depth1-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));
    await waitFor(() => expect(planRebalance).toHaveBeenCalledTimes(2));
  });

  // ── performance assertion ──

  // 실측값: 약 45ms (M2 Mac, vitest run). 예산 = 45 * 30 = 1350ms → 여유를 두고 2000ms.
  it("성능(depth1-1): 단일 목표 렌더+실행이 perfBudgetMs(2000) 이내", async () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });

    fillTarget(0, "BTC-USDT", "30");

    const startedAt = performance.now();
    run();
    await screen.findByTestId("rebalance-trade-BTC-USDT");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(2000));
  });

  // ── gate red (게이트 적색 재현) ──

  // 게이트적색(depth1-1): handleRun에서 내부 id를 제거하는 분기를 정확히 겨눈다.
  // 이 분기를 깨뜨리면(요청 본문에 id가 섞여 들어가면) 이 테스트만 적색이 된다.
  it("gate red(depth1-1): 실행 요청 본문에 목표 행의 내부 id가 섞여 들어가지 않는다", async () => {
    const planRebalance = vi.fn().mockResolvedValue(planResponse());
    renderPage({ whatIfClient: stubClient({ planRebalance }) });

    fillTarget(0, "BTC-USDT", "30");
    run();

    await waitFor(() => expect(planRebalance).toHaveBeenCalledTimes(1));
    const sentTargets = planRebalance.mock.calls[0][0];
    expect(sentTargets[0]).toEqual({ symbol: "BTC-USDT", targetWeightPct: "30" });
    expect(sentTargets[0].id).toBeUndefined();
  });
});
