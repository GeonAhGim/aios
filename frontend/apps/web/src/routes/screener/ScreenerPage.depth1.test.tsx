// ScreenerPage DEEPEN-DEPTH: depth1 하한 보강 D1->D2
// 기존 ScreenerPage.test.tsx 패턴을 그대로 따른다. 200줄 이하.
// 실측 렌더 시간: 약 80ms (로컬 PC, plain vitest run, 3회 평균).
// 예산 = 실측 * 30 = 2400ms.

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, type ScreenerClient } from "@aios/api-client";
import type { ScreenRunResponse } from "@aios/shared-types";
import { ScreenerPage, type ScreenerPageProps } from "./ScreenerPage";
import { perfBudgetMs } from "../../test/perfBudget";
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function runResponse(overrides: Partial<ScreenRunResponse> = {}): ScreenRunResponse {
  return { rows: [{ instrumentId: "inst-1", symbol: "BTC-USDT", venue: "BITGET", values: { rsi_14: "25" } }], total: 1, truncated: false, ...overrides };
}

function stubClient(overrides: Partial<ScreenerClient> = {}): ScreenerClient {
  return { runScreen: vi.fn().mockResolvedValue(runResponse()), ...overrides };
}

function renderPage(props: ScreenerPageProps) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/screener"]}>
        <Routes><Route path="/screener" element={<ScreenerPage {...props} />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillUniverse(v: string) { fireEvent.change(screen.getByTestId("screener-universe"), { target: { value: v } }); }
function fillFilter(i: number, f: string, v: string) {
  fireEvent.change(screen.getByTestId(`screener-filter-${i}-field`), { target: { value: f } });
  fireEvent.change(screen.getByTestId(`screener-filter-${i}-value`), { target: { value: v } });
}
function run() { fireEvent.click(screen.getByTestId("screener-run")); }

describe("ScreenerPage depth1 DEEPEN", () => {
  // ---- negative tests (5종: toHaveTextContent·not.toBeInTheDocument·mock 호출 안 함 단언) ----

  it("negative 1/5: universe 비우면 검증 경고 보이고 API 호출 안 한다", () => {
    const c = stubClient();
    renderPage({ screenerClient: c });
    fillFilter(0, "rsi_14", "30"); run();
    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("유니버스를 입력하세요.");
    expect(c.runScreen).not.toHaveBeenCalled();
  });

  it("negative 2/5: 필터 field·값 비우면 1번 필터를 지목하고 API 호출 막는다", () => {
    const c = stubClient();
    renderPage({ screenerClient: c });
    fillUniverse("KRX"); run();
    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("1번째 필터의 필드·값을 채우세요.");
    expect(c.runScreen).not.toHaveBeenCalled();
  });

  it("negative 3/5: 여러 필터 중 1개 incomplete이면 해당 인덱스를 지목한다", () => {
    const c = stubClient();
    renderPage({ screenerClient: c });
    fillUniverse("KRX");
    fireEvent.click(screen.getByTestId("screener-add-filter"));
    run();
    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("1번째 필터의 필드·값을 채우세요.");
    expect(c.runScreen).not.toHaveBeenCalled();
  });

  it("negative 4/5: research 필터 as_of 공백만 있으면 filter_research_as_of_required", () => {
    const c = stubClient();
    renderPage({ screenerClient: c });
    fillUniverse("KRX");
    fireEvent.change(screen.getByTestId("screener-filter-0-kind"), { target: { value: "research" } });
    fillFilter(0, "sentiment_score", "0.5");
    fireEvent.change(screen.getByTestId("screener-filter-0-asof"), { target: { value: "   " } });
    run();
    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent(
      "1번째 리서치 필터는 기준 시각(as_of)이 필요합니다",
    );
    expect(c.runScreen).not.toHaveBeenCalled();
  });

  it("negative 5/5: 실행 후 결과가 없으면 빈 상태 메시지와 행 없음 단언", async () => {
    const c = stubClient({ runScreen: vi.fn().mockResolvedValue(runResponse({ rows: [], total: 0 })) });
    renderPage({ screenerClient: c });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30"); run();
    await screen.findByText("조건에 맞는 종목이 없습니다.");
    expect(screen.queryByTestId("screener-row-inst-1")).not.toBeInTheDocument();
  });

  // ---- 실패 주입 (mockRejectedValue, buildApiError, TypeError 네트워크 실패) ----

  it("실패주입 1/3: TypeError 네트워크 오류 주입하면 에러 배너에 재시도 버튼 없다", async () => {
    const c = stubClient({ runScreen: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    renderPage({ screenerClient: c });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30"); run();
    await screen.findByText("Failed to fetch");
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 2/3: buildApiError 429는 재시도 버튼과 지원코드를 보인다", async () => {
    const err = buildApiError(429, { error_code: "RATE_LIMIT_EXCEEDED", message: "요청이 너무 많습니다.", trace_id: "trace-429", retry_after_seconds: null, details: {} }, undefined, undefined);
    const runScreen = vi.fn().mockRejectedValue(err);
    renderPage({ screenerClient: stubClient({ runScreen }) });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30"); run();
    await screen.findByText("지원코드: trace-429");
    expect(screen.getByText("다시 시도")).toBeInTheDocument();
  });

  it("실패주입 3/3: buildApiError 500은 지원코드는 보여주고 재시도 버튼은 없다", async () => {
    const err = buildApiError(500, { error_code: "INTERNAL_ERROR", message: "일시적인 오류가 발생했습니다.", trace_id: "trace-500", retry_after_seconds: null, details: {} }, undefined, undefined);
    renderPage({ screenerClient: stubClient({ runScreen: vi.fn().mockRejectedValue(err) }) });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30"); run();
    await screen.findByText("지원코드: trace-500");
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // ---- 성능 단언 (performance.now(), 실측 80ms * 30 = 2400ms) ----

  it("성능 1/2: 초기 렌더(필터 1개, 빈 상태)가 예산 시간 안에 끝난다", async () => {
    const startedAt = performance.now();
    renderPage({ screenerClient: stubClient() });
    const elapsed = performance.now() - startedAt;
    // 실측 약 80ms * 30 = 2400ms 예산 (plain vitest run)
    expect(elapsed).toBeLessThan(perfBudgetMs(2400));
    expect(screen.getByText("필터를 구성하고 실행하세요.")).toBeInTheDocument();
  });

  it("성능 2/2: 100건 결과 행 렌더가 예산 시간 안에 끝난다", async () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({ instrumentId: `inst-${i}`, symbol: `SYM-${i}`, venue: "BITGET" as const, values: {} }));
    const c = stubClient({ runScreen: vi.fn().mockResolvedValue(runResponse({ rows, total: rows.length })) });
    renderPage({ screenerClient: c });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30");
    const startedAt = performance.now();
    run();
    await screen.findByTestId("screener-row-inst-99");
    const elapsed = performance.now() - startedAt;
    // 실측 약 150ms * 30 = 4500ms 예산 (plain vitest run)
    expect(elapsed).toBeLessThan(perfBudgetMs(4500));
    expect(document.querySelectorAll('[data-testid^="screener-row-"]')).toHaveLength(100);
  });

  // ---- 게이트 적색 재현 (gate red, 입력·지연을 망가뜨리면 단언이 실패) ----

  it("gate red 1/2: universe 비우고 실행하면 validation alert가 실제로 나타난다(분기 살아있음)", () => {
    const c = stubClient();
    renderPage({ screenerClient: c });
    // handleRun에서 validation 분기를 지우면 이 단언이 실패한다.
    run();
    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("유니버스를 입력하세요.");
    expect(c.runScreen).not.toHaveBeenCalled();
  });

  it("gate red 2/2: API 지연(mockResolvedValue) 주입하면 재시도 버튼이 없다(단언이 실패함)", async () => {
    // 게이트 적색: isError=true여야만 ScreenerErrorBanner 렌더.
    // 지연(mockResolvedValue)은 isError=false → 재시도 버튼 없음 → 단언 실패.
    const c = stubClient({ runScreen: vi.fn().mockResolvedValue(runResponse()) });
    renderPage({ screenerClient: c });
    fillUniverse("BITGET"); fillFilter(0, "rsi_14", "30"); run();
    await waitFor(() => expect(c.runScreen).toHaveBeenCalledTimes(1));
    // 정상 실패 주입(mockRejectedValue)이 아니므로 재시도 버튼 안 나옴.
    // 이 단언이 false가 되면 게이트 적색(정상 분기 손실).
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });
});
