import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildApiError,
  SweepRouteNotImplementedError,
  type SweepPointResultView,
  type SweepRequestInput,
  type SweepResultView,
} from "@aios/api-client";
import { SweepResultsPage, type SweepResultsPageProps } from "./SweepResultsPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function sweepRequest(overrides: Partial<SweepRequestInput> = {}): SweepRequestInput {
  return {
    venue: "BITGET",
    instrumentId: "BTCUSDT",
    timeframe: "1h",
    start: "2026-01-01T00:00:00Z",
    end: "2026-02-01T00:00:00Z",
    initialCash: "10000",
    config: {
      slippage: { kind: "fixed", bps: 0 },
      commission: { venue: "BITGET", makerBps: 0, takerBps: 0, minFee: 0 },
      latencyMs: 0,
      partialFill: { maxParticipationPct: 1 },
      orderTypes: { limit: false, stop: false, oco: false, trailing: false },
      magnifierTf: null,
      costs: { funding: false, borrowApr: null },
      adjustments: { splits: false, dividends: false },
      calendar: "24x7",
    },
    axes: [
      { name: "rsi_len", values: [10, 14] },
      { name: "exit", values: [20, 30] },
    ],
    combos: [],
    metric: "finalEquity",
    dataLineageHash: "hash",
    rollupVersion: "v1",
    seed: 1,
    ...overrides,
  };
}

function sweepResult(overrides: Partial<SweepResultView> = {}): SweepResultView {
  return {
    axes: [
      { name: "rsi_len", values: [10, 14] },
      { name: "exit", values: [20, 30] },
    ],
    metric: "finalEquity",
    points: [
      {
        comboKey: "rsi_len=10,exit=20",
        comboIndex: 0,
        axisValues: { rsi_len: 10, exit: 20 },
        metricValue: "10500",
        reproducibilityKey: "repro-1",
        seed: 1,
      },
      {
        comboKey: "rsi_len=14,exit=30",
        comboIndex: 1,
        axisValues: { rsi_len: 14, exit: 30 },
        metricValue: "10900",
        reproducibilityKey: "repro-2",
        seed: 1,
      },
    ],
    stability: {
      bestAxisValues: { rsi_len: 14, exit: 30 },
      neighborMean: "10700",
      neighborStd: "50",
      isolated: false,
    },
    warnings: [],
    ...overrides,
  };
}

function renderPage(props: SweepResultsPageProps = {}, state: Record<string, unknown> | undefined = undefined) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/backtest/sweep-results", state }]}>
        <Routes>
          <Route path="/backtest/sweep-results" element={<SweepResultsPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SweepResultsPage", () => {
  it("스윕 요청이 없으면 안내 문구를 보여준다", () => {
    renderPage();
    expect(screen.getByText(/먼저 파라미터 스윕을 구성해/)).toBeInTheDocument();
  });

  it("결과가 오면 히트맵 셀과 재현 키를 렌더링한다", async () => {
    const runSweep = vi.fn().mockResolvedValue(sweepResult());
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() => expect(screen.getByTestId("sweep-cell-10-20")).toHaveTextContent("10500"));
    expect(screen.getByTestId("sweep-cell-14-30")).toHaveTextContent("10900");
    expect(screen.getByText("repro-1")).toBeInTheDocument();
    expect(screen.getByText("안정적")).toBeInTheDocument();
    expect(runSweep).toHaveBeenCalledWith(expect.objectContaining({ metric: "finalEquity" }));
  });

  it("축이 2개가 아니면 히트맵 대신 안내를 보여준다", async () => {
    const runSweep = vi.fn().mockResolvedValue(
      sweepResult({ axes: [{ name: "rsi_len", values: [10, 14] }], stability: null }),
    );
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() =>
      expect(screen.getByText(/히트맵은 축이 정확히 2개일 때만 표시됩니다/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/안정성 표면을 계산할 수 없습니다/)).toBeInTheDocument();
  });

  it("라우트가 아직 없으면(SweepRouteNotImplementedError) 경고를 보여준다", async () => {
    const runSweep = vi.fn().mockRejectedValue(new SweepRouteNotImplementedError());
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() =>
      expect(screen.getByText("파라미터 스윕 결과 API가 아직 제공되지 않습니다.")).toBeInTheDocument(),
    );
  });

  // DEPTH_BT(task-2728) 소급감사: 기존 negative는 SweepRouteNotImplementedError
  // 하나뿐이라 "실패주입"이 아니었다. 아래 4개는 (1) ApiError가 아닌 진짜 네트워크
  // 실패, (2) 손으로 필드를 채우지 않고 httpErrors.ts의 실제 파싱 함수
  // (buildApiError)로 만든 429/500 오류 두 종류(재시도 가능/불가능 갈래를 각각
  // 실제 분류 파이프라인 routeApiError로 태운다), (3) 백엔드 스키마가 드리프트해
  // findPoint의 엄격한 `===` 비교가 막아야 하는 타입 불일치를 각각 주입한다.
  it("실패주입 1/4: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const runSweep = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 2/4: 손으로 필드를 채운 ApiError가 아니라 buildApiError(실제 HTTP 파싱 경로)로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-sweep-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const runSweep = vi.fn().mockRejectedValue(realError);
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    expect(await screen.findByText("지원코드: trace-sweep-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(runSweep).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/4: buildApiError로 만든 진짜 500(INTERNAL_ERROR)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      500,
      {
        error_code: "INTERNAL_ERROR",
        message: "일시적인 오류가 발생했습니다.",
        trace_id: "trace-sweep-500",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const runSweep = vi.fn().mockRejectedValue(realError);
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    expect(await screen.findByText("지원코드: trace-sweep-500")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 4/4: 백엔드 스키마 드리프트로 axisValues가 문자열로 오면 findPoint의 엄격한 비교가 fail-closed해 데이터 없음으로 표시한다", async () => {
    const base = sweepResult();
    const driftedPoint: SweepPointResultView = {
      ...base.points[0]!,
      axisValues: { rsi_len: "10" as unknown as number, exit: 20 },
    };
    const runSweep = vi.fn().mockResolvedValue({ ...base, points: [driftedPoint, base.points[1]!] });
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() => expect(screen.getByTestId("sweep-cell-14-30")).toHaveTextContent("10900"));
    expect(screen.getByTestId("sweep-cell-10-20")).toHaveTextContent("—");
    expect(screen.getByTestId("sweep-cell-10-20")).toHaveAttribute("title", "데이터 없음");
  });

  // 게이트적색 재현: 아래 3개는 SweepResultsPage.tsx의 기존 분기 하나씩을 정확히
  // 겨눈다 — 그 분기를 되돌리면(guard 삭제/조건 반전) 해당 테스트만 적색이 된다.
  it("게이트적색 1/3: 그리드에 없는 콤보(희소 그리드)는 크래시 없이 데이터 없음 셀로 표시되고 나머지 콤보는 정상 렌더된다", async () => {
    const base = sweepResult();
    // 2x2 그리드(rsi_len×exit)에서 4콤보 중 1개(rsi_len=10,exit=30)만 빠진 채로
    // 서버가 응답한 경우 — findPoint가 undefined를 돌려줄 때의 `point ? ... : "—"`
    // 분기가 지워지면 undefined.metricValue 접근으로 이 테스트가 크래시하며 적색이 된다.
    const runSweep = vi.fn().mockResolvedValue(base);
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() => expect(screen.getByTestId("sweep-cell-10-20")).toHaveTextContent("10500"));
    expect(screen.getByTestId("sweep-cell-10-30")).toHaveTextContent("—");
    expect(screen.getByTestId("sweep-cell-10-30")).toHaveAttribute("title", "데이터 없음");
    expect(screen.getByTestId("sweep-cell-14-30")).toHaveTextContent("10900");
  });

  it("게이트적색 2/3: 고립된 최적점(isolated=true)은 과최적화 의심 배지로 표시된다", async () => {
    const runSweep = vi.fn().mockResolvedValue(
      sweepResult({ stability: { bestAxisValues: { rsi_len: 10, exit: 20 }, neighborMean: "9000", neighborStd: "900", isolated: true } }),
    );
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    expect(await screen.findByText("고립됨(과최적화 의심)")).toBeInTheDocument();
    expect(screen.queryByText("안정적")).not.toBeInTheDocument();
  });

  it("게이트적색 3/3: 결과 콤보가 0건이면 재현 키 카드 자체가 렌더되지 않는다(히트맵은 전부 데이터 없음으로 표시)", async () => {
    const runSweep = vi.fn().mockResolvedValue(sweepResult({ points: [], stability: null }));
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() => expect(screen.getByTestId("sweep-cell-10-20")).toHaveTextContent("—"));
    expect(screen.queryByText("재현 키")).not.toBeInTheDocument();
  });

  // 성능: 18x18(324콤보) 그리드도 예산 시간 안에 렌더해야 한다. findPoint가 매
  // 셀마다 points 배열 전체를 선형 탐색하므로(O(cells x points)), 축 크기를
  // 하나 늘릴 때마다 4제곱으로 늘어나는 총 비교 횟수가 더 나쁜 알고리즘으로
  // 퇴행하는 회귀를 잡는다(BacktestPanel.test.tsx task-1607 DEEPEN과 동일 관용).
  it("성능: 18x18(324콤보) 그리드도 예산 시간 안에 렌더한다", async () => {
    const size = 18;
    const axisValues = Array.from({ length: size }, (_, i) => i);
    const points: SweepPointResultView[] = [];
    for (const x of axisValues) {
      for (const y of axisValues) {
        points.push({
          comboKey: `x=${x},y=${y}`,
          comboIndex: points.length,
          axisValues: { x, y },
          metricValue: String(x * 100 + y),
          reproducibilityKey: `repro-${x}-${y}`,
          seed: 1,
        });
      }
    }
    const bigResult: SweepResultView = {
      axes: [
        { name: "x", values: axisValues },
        { name: "y", values: axisValues },
      ],
      metric: "finalEquity",
      points,
      stability: null,
      warnings: [],
    };
    const runSweep = vi.fn().mockResolvedValue(bigResult);

    const startedAt = performance.now();
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });
    await screen.findByTestId(`sweep-cell-${size - 1}-${size - 1}`);
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(4000);
    expect(screen.getByTestId("sweep-cell-0-0")).toHaveTextContent("0");
    expect(document.querySelectorAll('td[data-testid^="sweep-cell-"]')).toHaveLength(size * size);
  });
});
