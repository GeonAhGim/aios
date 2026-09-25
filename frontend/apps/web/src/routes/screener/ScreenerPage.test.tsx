import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, ScreenerRouteNotImplementedError, type ScreenerClient } from "@aios/api-client";
import type { ScreenRunResponse } from "@aios/shared-types";
import { ScreenerPage, type ScreenerPageProps } from "./ScreenerPage";
import { perfBudgetMs } from "../../test/perfBudget";
// i18n/index.ts(task-2685)의 initI18n()이 모듈 로드 시 1회 부수효과로 실행된다 — 이
// 화면은 문구를 전부 useTranslation()의 t(key)로 조회하므로(check_i18n_literals.mjs
// 게이트), 테스트도 같은 부수효과를 먼저 일으키지 않으면 t()가 키 문자열 그대로를
// 반환해 렌더된 한국어 문구를 기대하는 단언이 전부 깨진다(AiStudioPage.test.tsx와
// 동일 사유).
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function runResponse(overrides: Partial<ScreenRunResponse> = {}): ScreenRunResponse {
  return {
    rows: [
      { instrumentId: "inst-1", symbol: "BTC-USDT", venue: "BITGET", values: { rsi_14: "25" } },
    ],
    total: 1,
    truncated: false,
    ...overrides,
  };
}

function stubClient(overrides: Partial<ScreenerClient> = {}): ScreenerClient {
  return {
    runScreen: vi.fn().mockResolvedValue(runResponse()),
    ...overrides,
  };
}

function renderPage(props: ScreenerPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/screener"]}>
        <Routes>
          <Route path="/screener" element={<ScreenerPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillUniverse(value: string) {
  fireEvent.change(screen.getByTestId("screener-universe"), { target: { value } });
}

function fillFilter(index: number, field: string, value: string) {
  fireEvent.change(screen.getByTestId(`screener-filter-${index}-field`), { target: { value: field } });
  fireEvent.change(screen.getByTestId(`screener-filter-${index}-value`), { target: { value } });
}

function run() {
  fireEvent.click(screen.getByTestId("screener-run"));
}

describe("ScreenerPage", () => {
  it("실행 전에는 안내만 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPage({ screenerClient: client });

    expect(screen.getByText("필터를 구성하고 실행하세요.")).toBeInTheDocument();
    expect(client.runScreen).not.toHaveBeenCalled();
  });

  it("negative 1/3: universe가 비어 있으면 검증 오류를 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPage({ screenerClient: client });

    fillFilter(0, "rsi_14", "30");
    run();

    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("유니버스를 입력하세요.");
    expect(client.runScreen).not.toHaveBeenCalled();
  });

  it("negative 2/3: 필터의 필드·값이 비어 있으면 1번째 필터를 지목하는 검증 오류를 보여준다", () => {
    const client = stubClient();
    renderPage({ screenerClient: client });

    fillUniverse("KRX");
    run();

    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent("1번째 필터의 필드·값을 채우세요.");
    expect(client.runScreen).not.toHaveBeenCalled();
  });

  it("negative 3/3: 리서치 필터에 기준 시각(as_of)이 없으면 실행이 막힌다(누수 차단)", () => {
    const client = stubClient();
    renderPage({ screenerClient: client });

    fillUniverse("KRX");
    fireEvent.change(screen.getByTestId("screener-filter-0-kind"), { target: { value: "research" } });
    fillFilter(0, "sentiment_score", "0.5");
    run();

    expect(screen.getByTestId("screener-validation-alert")).toHaveTextContent(
      "1번째 리서치 필터는 기준 시각(as_of)이 필요합니다",
    );
    expect(client.runScreen).not.toHaveBeenCalled();
  });

  it("결과가 없으면 빈 상태를 보여준다(크래시 없음)", async () => {
    const client = stubClient({ runScreen: vi.fn().mockResolvedValue(runResponse({ rows: [], total: 0 })) });
    renderPage({ screenerClient: client });

    fillUniverse("KRX");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByText("조건에 맞는 종목이 없습니다.")).toBeInTheDocument();
  });

  it("유효한 필터로 실행하면 결과 행과 차트/백테스트 연결 링크를 보여준다", async () => {
    const client = stubClient();
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByTestId("screener-row-inst-1")).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    const link = screen.getByText("차트/백테스트에서 보기").closest("a");
    expect(link).toHaveAttribute("href", "/chart?instrument_id=inst-1");
  });

  it("결과가 1,000행 상한에 도달하면(truncated) 경고를 보여준다", async () => {
    const client = stubClient({
      runScreen: vi.fn().mockResolvedValue(runResponse({ truncated: true })),
    });
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByText("결과가 1,000행 상한에 도달해 잘렸습니다.")).toBeInTheDocument();
  });

  it("라우트가 아직 없으면(ScreenerRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      runScreen: vi.fn().mockRejectedValue(new ScreenerRouteNotImplementedError()),
    });
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByText("스크리너 실행 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 실패주입: FollowPage.test.tsx(DEPTH_UX task-2699) 관용과 동일하게 (1) ApiError가
  // 아닌 진짜 네트워크 실패, (2)(3) buildApiError(실제 HTTP 파싱 경로)로 만든
  // 429/500을 각각 주입해 재시도 가능/불가능 갈래를 실제 분류 파이프라인
  // (routeApiError)으로 태운다.
  it("실패주입 1/3: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({ runScreen: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
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
        trace_id: "trace-screener-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const runScreen = vi.fn().mockRejectedValue(realError);
    renderPage({ screenerClient: stubClient({ runScreen }) });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByText("지원코드: trace-screener-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(runScreen).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/3: buildApiError로 만든 500(INTERNAL_ERROR)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      500,
      {
        error_code: "INTERNAL_ERROR",
        message: "일시적인 오류가 발생했습니다.",
        trace_id: "trace-screener-500",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    renderPage({ screenerClient: stubClient({ runScreen: vi.fn().mockRejectedValue(realError) }) });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    expect(await screen.findByText("지원코드: trace-screener-500")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("필터 추가·제거가 행 개수를 바꾼다", () => {
    renderPage({ screenerClient: stubClient() });

    fireEvent.click(screen.getByTestId("screener-add-filter"));
    expect(screen.getByTestId("screener-filter-row-1")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("screener-filter-1-remove"));
    expect(screen.queryByTestId("screener-filter-row-1")).not.toBeInTheDocument();
  });

  // 게이트적색: 아래 2개는 ScreenerPage.tsx의 특정 분기를 정확히 겨눈다 — 그 분기를
  // 되돌리면 해당 테스트만 적색이 된다.
  it("게이트적색 1/2: 실행 요청 본문에는 필터 행의 내부 id가 섞여 들어가지 않는다(buildDefinition의 id 제거 분기)", async () => {
    const runScreen = vi.fn().mockResolvedValue(runResponse());
    renderPage({ screenerClient: stubClient({ runScreen }) });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    await waitFor(() => expect(runScreen).toHaveBeenCalledTimes(1));
    const sentDefinition = runScreen.mock.calls[0][0];
    expect(sentDefinition.filters[0]).toEqual({ kind: "indicator", field: "rsi_14", operator: "gt", value: "30" });
    expect(sentDefinition.filters[0].id).toBeUndefined();
  });

  it("게이트적색 2/2: 결과 행 링크는 instrumentId를 URL 인코딩해 /chart?instrument_id=로 연결한다", async () => {
    const client = stubClient({
      runScreen: vi.fn().mockResolvedValue(
        runResponse({
          rows: [{ instrumentId: "inst/needs encoding", symbol: "ETH-USDT", venue: "BITGET", values: {} }],
        }),
      ),
    });
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");
    run();

    const link = await screen.findByText("차트/백테스트에서 보기");
    expect(link.closest("a")).toHaveAttribute("href", "/chart?instrument_id=inst%2Fneeds%20encoding");
  });

  // 성능: 200건의 결과 행도 예산 시간 안에 렌더해야 한다(idempotency.test.ts
  // task-2730 DEEPEN 관용과 동일하게 수치 예산으로 못박는다).
  it("성능: 200건의 결과 행도 예산 시간 안에 렌더한다", async () => {
    const rows = Array.from({ length: 200 }, (_, i) => ({
      instrumentId: `inst-${i}`,
      symbol: `SYM-${i}`,
      venue: "BITGET" as const,
      values: {},
    }));
    const client = stubClient({ runScreen: vi.fn().mockResolvedValue(runResponse({ rows, total: rows.length })) });
    renderPage({ screenerClient: client });

    fillUniverse("BITGET");
    fillFilter(0, "rsi_14", "30");

    const startedAt = performance.now();
    run();
    await screen.findByTestId("screener-row-inst-199");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="screener-row-"]')).toHaveLength(200);
  });
});
