import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, buildApiError } from "@aios/api-client";
import type { BacktestFillView, QuickBacktestResultView } from "@aios/api-client";
import type { CandlestickPoint } from "@aios/ui-web";
import { BacktestPanel, type RunQuickBacktest } from "./BacktestPanel";
import { perfBudgetMs } from "../../test/perfBudget";
import { apiClient } from "@aios/shared-hooks";
import type { BacktestPanelProps } from "./BacktestPanel";
import { materializeSweepGrid } from "./sweepGrid";

const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async (original) => ({ ...await original<typeof import("react-router-dom")>(), useNavigate: () => navigate }));

const T0 = Math.floor(Date.parse("2026-09-06T00:00:00Z") / 1000);
const T1 = T0 + 3600;
const POINTS: CandlestickPoint[] = [
  { time: T0, open: 100, high: 110, low: 90, close: 100 },
  { time: T1, open: 100, high: 120, low: 95, close: 105 },
];

function renderPanel(runQuickBacktest: RunQuickBacktest, points: readonly CandlestickPoint[] = POINTS, runSweep?: BacktestPanelProps["runSweep"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BacktestPanel
        venue="BITGET"
        instrumentId="instr-1"
        timeframe="1h"
        start="2026-09-05T00:00:00Z"
        end="2026-09-06T01:00:00Z"
        points={points}
        runQuickBacktest={runQuickBacktest}
        runSweep={runSweep}
      />
    </QueryClientProvider>,
  );
}

function okResult(): QuickBacktestResultView {
  return {
    fills: [
      {
        barIndex: 0,
        openTime: "2026-09-06T00:10:00Z",
        side: "BUY",
        orderType: "market",
        quantity: "0.5",
        price: "100.00",
        commission: "0.01",
        remainingQuantity: "0",
      },
    ],
    equityCurve: ["10000", "10005"],
    finalEquity: "10005",
    cash: "9950",
    positionQuantity: "0.5",
    fundingCost: "0",
    borrowCost: "0",
    bars: 2,
    expiredOrders: 0,
    warnings: [],
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  navigate.mockClear();
});

function fillSweep() {
  fireEvent.change(screen.getByTestId("sweep-lineage"), { target: { value: "lineage-1" } });
  fireEvent.change(screen.getByTestId("sweep-rollup"), { target: { value: "rollup-1" } });
}

describe("스윕 CTA", () => {
  function mockCompile() {
    return vi.spyOn(apiClient, "compileScript").mockResolvedValue({ scriptHash: "compiled-hash" } as Awaited<ReturnType<typeof apiClient.compileScript>>);
  }

  it("각 그리드 조합을 컴파일하고 성공한 요청을 결과 화면에 전달한다", async () => {
    const compile = mockCompile();
    const runSweep = vi.fn().mockResolvedValue({ axes: [], points: [], warnings: [], stability: null, metric: "final_equity" });
    renderPanel(vi.fn(), POINTS, runSweep);
    fillSweep();
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(compile).toHaveBeenCalledTimes(3);
    expect(compile.mock.calls[0][0]).toContain("input length: int = 7");
    const request = runSweep.mock.calls[0][0];
    expect(request).toMatchObject({ instrumentId: "instr-1", initialCash: "10000", dataLineageHash: "lineage-1", axes: [{ name: "length", values: [7,14,21] }] });
    expect(request.combos[0]).toMatchObject({ scriptHash: "compiled-hash", axisValues: { length: 7 } });
    expect(navigate).toHaveBeenCalledWith("/backtest/sweep-results", { state: { sweepRequest: request } });
  });

  it("실패주입: HTTP 500이면 에러를 표시하고 이동하지 않는다", async () => {
    mockCompile();
    const runSweep = vi.fn().mockRejectedValue(buildApiError(500, { error_code: "INTERNAL_ERROR", message: "sweep failed", trace_id: "sweep-500" }, undefined, undefined));
    renderPanel(vi.fn(), POINTS, runSweep);
    fillSweep();
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    expect(await screen.findByText("지원코드: sweep-500")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it.each(['{}', '{"length":[7,7]}', '{"missing":[7]}', '{"length":[1.5]}', JSON.stringify({ length: Array.from({length: 65}, (_, i) => i) })])("negative: 잘못된 그리드 %s는 API 전에 거부한다", async (grid) => {
    const compile = mockCompile();
    const runSweep = vi.fn();
    renderPanel(vi.fn(), POINTS, runSweep);
    fillSweep();
    fireEvent.change(screen.getByTestId("sweep-grid"), { target: { value: grid } });
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    expect(await screen.findByText(/그리드와 데이터 이력 정보를 확인하세요/)).toBeInTheDocument();
    expect(compile).not.toHaveBeenCalled();
    expect(runSweep).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("negative: 컴파일 실패 시 스윕을 제출하지 않는다", async () => {
    mockCompile().mockRejectedValue(new Error("compile failed"));
    const runSweep = vi.fn();
    renderPanel(vi.fn(), POINTS, runSweep);
    fillSweep();
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    expect(await screen.findByText("compile failed")).toBeInTheDocument();
    expect(runSweep).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("negative: 데이터 이력이 없으면 컴파일과 제출을 막는다", async () => {
    const compile = mockCompile();
    const runSweep = vi.fn();
    renderPanel(vi.fn(), POINTS, runSweep);
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    expect(await screen.findByText(/그리드와 데이터 이력 정보를 확인하세요/)).toBeInTheDocument();
    expect(compile).not.toHaveBeenCalled();
    expect(runSweep).not.toHaveBeenCalled();
  });

  it("negative: 스윕 처리 중 중복 실행을 막는다", async () => {
    mockCompile();
    const runSweep = vi.fn().mockReturnValue(new Promise(() => {}));
    renderPanel(vi.fn(), POINTS, runSweep);
    fillSweep();
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    await waitFor(() => expect(runSweep).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("backtest-sweep-run")).toBeDisabled();
    fireEvent.click(screen.getByTestId("backtest-sweep-run"));
    expect(runSweep).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("성능/경계: 2축 64조합을 100ms 예산 안에 생성하고 각 소스에 두 값을 반영한다", () => {
    const values = Array.from({ length: 8 }, (_, i) => i + 1);
    const startedAt = performance.now();
    const result = materializeSweepGrid(JSON.stringify({ length: values, exit: values }), "input length: int = 14\ninput exit: int = 20\n");
    expect(performance.now() - startedAt).toBeLessThan(perfBudgetMs(100));
    expect(result.combos).toHaveLength(64);
    expect(result.combos[63]).toMatchObject({ axisValues: { length: 8, exit: 8 }, scriptSource: "input length: int = 8\ninput exit: int = 8\n" });
  });
});

describe("BacktestPanel", () => {
  it("실행하면 결과 요약과 체결 마커 오버레이를 보여준다", async () => {
    const runQuickBacktest = vi.fn().mockResolvedValue(okResult());
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("backtest-summary")).toBeInTheDocument();
    expect(screen.getByText("10005")).toBeInTheDocument();
    expect(screen.getByLabelText("BUY 0.5 @ 100.00")).toBeInTheDocument();
    expect(runQuickBacktest.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ venue: "BITGET", instrumentId: "instr-1", scriptSource: expect.any(String) }),
    );
  });

  it("negative: 봉 상한 초과(TooManyBarsError, details.bars/max)는 BT-11 안내 문구를 보여준다", async () => {
    const runQuickBacktest = vi
      .fn()
      .mockRejectedValue(new ApiError(400, "raw", "trace-1", "VALIDATION_INVALID_FIELD", undefined, { bars: 6000, max: 5000 }));
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("봉 수 6000개가 즉시 백테스트 상한 5000개를 넘습니다. 전체 백테스트(BT-11)를 이용하세요.")).toBeInTheDocument();
  });

  it("negative: 스크립트 컴파일 오류(details.code/line/col)는 ScriptEditor 마커로 보여주고 일반 오류 배너는 숨긴다", async () => {
    const runQuickBacktest = vi
      .fn()
      .mockRejectedValue(
        new ApiError(400, "raw", "trace-2", "VALIDATION_INVALID_FIELD", undefined, {
          code: "SCRIPT_SYNTAX",
          line: 3,
          col: 7,
        }),
      );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("script-editor-marker-0")).toHaveTextContent("3행 7열: SCRIPT_SYNTAX");
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
  });

  it("negative: 표시할 캔들이 없으면 실행 버튼을 비활성화하고 안내를 보여준다", () => {
    const runQuickBacktest = vi.fn();
    renderPanel(runQuickBacktest, []);

    expect(screen.getByText("표시할 캔들이 없어 백테스트를 실행할 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-run")).toBeDisabled();
    expect(runQuickBacktest).not.toHaveBeenCalled();
  });

  it("negative: 실행 중에는 재요청을 막고, 취소하면 다시 실행할 수 있다", async () => {
    let resolveRun: (value: QuickBacktestResultView) => void = () => {};
    const runQuickBacktest = vi.fn().mockImplementation(
      () =>
        new Promise<QuickBacktestResultView>((resolve) => {
          resolveRun = resolve;
        }),
    );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => expect(screen.getByTestId("backtest-run")).toBeDisabled());
    expect(screen.getByTestId("backtest-cancel")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("backtest-cancel"));
    await waitFor(() => expect(screen.getByTestId("backtest-run")).not.toBeDisabled());

    fireEvent.click(screen.getByTestId("backtest-run"));
    await waitFor(() => expect(runQuickBacktest).toHaveBeenCalledTimes(2));
    resolveRun(okResult());
    expect(await screen.findByTestId("backtest-summary")).toBeInTheDocument();
  });

  // DEPTH_BT(task-2728) 소급감사: 기존 negative는 손수 만든 ApiError를
  // mockRejectedValue로 던져 "백엔드 400 응답을 흉내"만 냈다. 아래는 (1)
  // ApiError가 아닌 진짜 네트워크 실패, (2) 손으로 필드를 채우지 않고
  // httpErrors.ts의 실제 파싱 함수(buildApiError)로 만든 오류, (3) 백엔드
  // 스키마가 드리프트해 타입가드가 막아야 하는 잘못된 모양의 details를 각각
  // 주입해 실제 실패 경로를 검증한다.
  it("실패주입: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여준다", async () => {
    const runQuickBacktest = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
    expect(screen.queryByTestId("script-editor-marker-0")).not.toBeInTheDocument();
  });

  it("실패주입: 손으로 필드를 채운 ApiError가 아니라 buildApiError(실제 HTTP 파싱 경로)로 만든 429 오류도 처리한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-real-429",
        retry_after_seconds: 5,
        details: {},
      },
      undefined,
      undefined,
    );
    const runQuickBacktest = vi.fn().mockRejectedValue(realError);
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("지원코드: trace-real-429")).toBeInTheDocument();
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
  });

  it("실패주입: 백엔드가 details.bars를 문자열로 보내는 스키마 드리프트에도 타입가드가 fail-closed해 일반 배너로 대체한다", async () => {
    const runQuickBacktest = vi
      .fn()
      .mockRejectedValue(
        new ApiError(400, "raw", "trace-drift", "VALIDATION_INVALID_FIELD", undefined, { bars: "6000", max: 5000 }),
      );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("입력값을 확인해주세요.")).toBeInTheDocument();
    expect(screen.queryByText(/전체 백테스트\(BT-11\)/)).not.toBeInTheDocument();
  });

  it("실패주입: 프론트가 아직 모르는 신규 컴파일 오류 코드는 마커 대신 일반 배너로 fail-closed한다", async () => {
    const runQuickBacktest = vi.fn().mockRejectedValue(
      new ApiError(400, "raw", "trace-unknown-code", "VALIDATION_INVALID_FIELD", undefined, {
        code: "SCRIPT_NEW_UNKNOWN_CODE",
        line: 3,
        col: 7,
      }),
    );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("입력값을 확인해주세요.")).toBeInTheDocument();
    expect(screen.queryByTestId("script-editor-marker-0")).not.toBeInTheDocument();
  });

  // 게이트적색 재현: fillMarkers는 `!Number.isFinite(time) || time < from || time
  // > to`로 걸러낸다. openTime을 파싱할 수 없는 체결이 섞여도(진짜 Date 파싱
  // 실패, mock으로 흉내낸 게 아니다) 그 체결만 조용히 빠지고 나머지는 그대로
  // 그려져야 한다 — 이 !Number.isFinite(time) 가드를 지우면 NaN 좌표
  // 마커(title에 "SELL 0.5 @ 101.00")가 나타나 이 테스트가 적색이 된다.
  it("실패주입/게이트적색: openTime을 파싱할 수 없는 체결이 섞여도 그 체결만 제외하고 나머지는 정상 렌더한다", async () => {
    const base = okResult();
    const brokenFill: BacktestFillView = {
      ...base.fills[0]!,
      barIndex: 1,
      side: "SELL",
      price: "101.00",
      openTime: "not-a-real-timestamp",
    };
    const runQuickBacktest = vi.fn().mockResolvedValue({ ...base, fills: [base.fills[0]!, brokenFill] });
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("backtest-markers-overlay")).toBeInTheDocument();
    expect(screen.getByLabelText("BUY 0.5 @ 100.00")).toBeInTheDocument();
    expect(screen.queryByLabelText("SELL 0.5 @ 101.00")).not.toBeInTheDocument();
  });

  // 게이트적색 재현: timeDomain 경계는 `time < from || time > to`(strict)로
  // 판정한다. 체결 시각이 마지막 캔들 시각(to)과 정확히 같은 경계값도 포함돼야
  // 한다 — 이 조건을 <=/>=로 되돌리면(off-by-one 회귀) 아래 마커가 사라져 이
  // 테스트가 적색이 된다.
  it("게이트적색: 체결 시각이 timeDomain 상한과 정확히 같은 경계값이어도 마커가 그려진다", async () => {
    const base = okResult();
    const boundaryFill: BacktestFillView = { ...base.fills[0]!, openTime: new Date(T1 * 1000).toISOString() };
    const runQuickBacktest = vi.fn().mockResolvedValue({ ...base, fills: [boundaryFill] });
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("backtest-markers-overlay")).toBeInTheDocument();
    expect(screen.getByLabelText("BUY 0.5 @ 100.00")).toBeInTheDocument();
  });

  // 성능: 체결 500건(마커 500개)이 와도 예산 시간 안에 렌더가 끝나야 한다.
  // fillMarkers 매핑이나 마커 렌더가 O(n^2)로 퇴행하면(예: 매 항목마다 배열을
  // 재순회) 이 예산을 넘겨 적발된다 — ScriptEditor.test.tsx(1000줄/200마커,
  // task-1606 DEEPEN)와 동일한 관용이다.
  it("성능: 체결 500건도 예산 시간 안에 렌더하고 O(n^2) 퇴행 회귀를 잡는다", async () => {
    const base = okResult();
    const manyFills: BacktestFillView[] = Array.from({ length: 500 }, (_, i) => ({
      ...base.fills[0]!,
      barIndex: i,
      openTime: new Date((T0 + i) * 1000).toISOString(),
      price: String(100 + (i % 50)),
    }));
    const runQuickBacktest = vi.fn().mockResolvedValue({ ...base, fills: manyFills });
    renderPanel(runQuickBacktest);

    const startedAt = performance.now();
    fireEvent.click(screen.getByTestId("backtest-run"));
    await screen.findByTestId("backtest-markers-overlay");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(3000));
    expect(screen.getAllByRole("img")).toHaveLength(500);
  });
});
