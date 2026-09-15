import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, WhatIfRouteNotImplementedError, type WhatIfClient } from "@aios/api-client";
import type { WhatIfImpactResponse } from "@aios/shared-types";
import { WhatIfPanel, type WhatIfPanelProps } from "./WhatIfPanel";
// i18n/index.ts(task-2685)의 initI18n()이 모듈 로드 시 1회 부수효과로 실행된다 — 이
// 화면은 문구를 전부 useTranslation()의 t(key)로 조회하므로(check_i18n_literals.mjs
// 게이트), 테스트도 같은 부수효과를 먼저 일으키지 않으면 t()가 키 문자열 그대로를
// 반환해 렌더된 한국어 문구를 기대하는 단언이 전부 깨진다(ScreenerPage.test.tsx와
// 동일 사유).
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function impactResponse(overrides: Partial<WhatIfImpactResponse> = {}): WhatIfImpactResponse {
  return {
    exposureDeltaPct: "1.5",
    concentrationDeltaPct: "0.3",
    varDelta: "120.00",
    limitHeadroomDelta: "-50.00",
    ...overrides,
  };
}

function stubClient(overrides: Partial<WhatIfClient> = {}): WhatIfClient {
  return {
    previewOrder: vi.fn().mockResolvedValue(impactResponse()),
    planRebalance: vi.fn(),
    ...overrides,
  };
}

function renderPanel(props: WhatIfPanelProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WhatIfPanel {...props} />
    </QueryClientProvider>,
  );
}

function fillOrder(symbol: string, quantity: string) {
  fireEvent.change(screen.getByTestId("whatif-symbol"), { target: { value: symbol } });
  fireEvent.change(screen.getByTestId("whatif-quantity"), { target: { value: quantity } });
}

function run() {
  fireEvent.click(screen.getByTestId("whatif-run"));
}

describe("WhatIfPanel", () => {
  it("실행 전에는 안내만 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPanel({ whatIfClient: client });

    expect(screen.getByText("주문을 입력하고 미리보기를 실행하세요.")).toBeInTheDocument();
    expect(client.previewOrder).not.toHaveBeenCalled();
  });

  it("negative 1/3: 종목이 비어 있으면 검증 오류를 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPanel({ whatIfClient: client });

    fillOrder("", "1");
    run();

    expect(screen.getByTestId("whatif-validation-alert")).toHaveTextContent("종목을 입력하세요.");
    expect(client.previewOrder).not.toHaveBeenCalled();
  });

  it("negative 2/3: 수량이 비어 있으면 검증 오류를 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPanel({ whatIfClient: client });

    fillOrder("BTC-USDT", "");
    run();

    expect(screen.getByTestId("whatif-validation-alert")).toHaveTextContent("수량은 0보다 큰 숫자여야 합니다.");
    expect(client.previewOrder).not.toHaveBeenCalled();
  });

  it("negative 3/3: 수량이 0 이하면(음수 포함) 검증 오류를 보여주고 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPanel({ whatIfClient: client });

    fillOrder("BTC-USDT", "-5");
    run();

    expect(screen.getByTestId("whatif-validation-alert")).toHaveTextContent("수량은 0보다 큰 숫자여야 합니다.");
    expect(client.previewOrder).not.toHaveBeenCalled();
  });

  it("유효한 주문으로 실행하면 영향 결과를 보여준다", async () => {
    const client = stubClient();
    renderPanel({ whatIfClient: client });

    fillOrder("BTC-USDT", "0.5");
    run();

    expect(await screen.findByTestId("whatif-impact-result")).toBeInTheDocument();
    expect(screen.getByTestId("whatif-impact-exposure")).toHaveTextContent("1.5%");
    expect(screen.getByTestId("whatif-impact-concentration")).toHaveTextContent("0.3%");
    expect(screen.getByTestId("whatif-impact-var")).toHaveTextContent("120.00");
    expect(screen.getByTestId("whatif-impact-limit-headroom")).toHaveTextContent("-50.00");
  });

  it("라우트가 아직 없으면(WhatIfRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      previewOrder: vi.fn().mockRejectedValue(new WhatIfRouteNotImplementedError("whatif.previewOrder")),
    });
    renderPanel({ whatIfClient: client });

    fillOrder("BTC-USDT", "0.5");
    run();

    expect(await screen.findByText("가상 주문 영향 미리보기 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 실패주입: ScreenerPage.test.tsx(DEPTH_UX task-2692) 관용과 동일하게 (1) ApiError가
  // 아닌 진짜 네트워크 실패, (2)(3) buildApiError(실제 HTTP 파싱 경로)로 만든
  // 429/500을 각각 주입해 재시도 가능/불가능 갈래를 실제 분류 파이프라인
  // (routeApiError)으로 태운다.
  it("실패주입 1/3: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({ previewOrder: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    renderPanel({ whatIfClient: client });

    fillOrder("BTC-USDT", "0.5");
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
        trace_id: "trace-whatif-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const previewOrder = vi.fn().mockRejectedValue(realError);
    renderPanel({ whatIfClient: stubClient({ previewOrder }) });

    fillOrder("BTC-USDT", "0.5");
    run();

    expect(await screen.findByText("지원코드: trace-whatif-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(previewOrder).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/3: buildApiError로 만든 500(INTERNAL_ERROR)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      500,
      {
        error_code: "INTERNAL_ERROR",
        message: "일시적인 오류가 발생했습니다.",
        trace_id: "trace-whatif-500",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    renderPanel({ whatIfClient: stubClient({ previewOrder: vi.fn().mockRejectedValue(realError) }) });

    fillOrder("BTC-USDT", "0.5");
    run();

    expect(await screen.findByText("지원코드: trace-whatif-500")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 게이트적색: 아래 2개는 WhatIfPanel.tsx의 특정 분기를 정확히 겨눈다 — 그 분기를
  // 되돌리면 해당 테스트만 적색이 된다.
  it("게이트적색 1/2: 매매 구분을 SELL로 바꾸고 실행하면 요청에 side: SELL이 그대로 전달된다(하드코딩 BUY 회귀 방지)", async () => {
    const previewOrder = vi.fn().mockResolvedValue(impactResponse());
    renderPanel({ whatIfClient: stubClient({ previewOrder }) });

    fillOrder("BTC-USDT", "0.5");
    fireEvent.change(screen.getByTestId("whatif-side"), { target: { value: "SELL" } });
    run();

    await waitFor(() => expect(previewOrder).toHaveBeenCalledTimes(1));
    expect(previewOrder.mock.calls[0][0]).toEqual({ symbol: "BTC-USDT", side: "SELL", quantity: "0.5" });
  });

  it("게이트적색 2/2: initialOrder가 주어지면 마운트 시 자동으로 미리보기를 호출한다(RebalancePage 연동 분기)", async () => {
    const previewOrder = vi.fn().mockResolvedValue(impactResponse());
    renderPanel({
      whatIfClient: stubClient({ previewOrder }),
      initialOrder: { symbol: "ETH-USDT", side: "BUY", quantity: "2" },
    });

    await waitFor(() => expect(previewOrder).toHaveBeenCalledTimes(1));
    expect(previewOrder).toHaveBeenCalledWith({ symbol: "ETH-USDT", side: "BUY", quantity: "2" });
    expect(await screen.findByTestId("whatif-impact-result")).toBeInTheDocument();
  });
});
