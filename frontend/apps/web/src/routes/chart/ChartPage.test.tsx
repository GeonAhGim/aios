import "./ChartPage.testHarness";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { VISIBLE_CANDLE_COUNT } from "./chartPageConfig";
import { perfBudgetMs } from "../../test/perfBudget";
import { apiErrorLike, fakeChartingPort, manyCandles, okResult, okResultWithCandles, renderPage } from "./ChartPage.testHarness";

describe("ChartPage", () => {
  it("정상 조회 시 CH-2 candleStream에 병합해 캔들스틱 차트를 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);

    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    expect(fetchCandles).toHaveBeenCalledWith(
      expect.objectContaining({ venue: "BITGET", instrumentId: "BTCUSDT", timeframe: "1h" }),
    );
  });

  it("negative: instrument_id 없이 진입하면 fetch하지 않고 안내만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles, null);

    expect(await screen.findByText(/심볼을 먼저 선택하세요/)).toBeInTheDocument();
    expect(fetchCandles).not.toHaveBeenCalled();
  });

  it("CH-7 재생 컨트롤의 다음 봉 버튼을 누르면 visible 구간만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "다음 봉" }));
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 1개"));

    fireEvent.click(screen.getByRole("button", { name: "다음 봉" }));
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 2개"));
  });

  it("CH-4 그리기 도구로 그리기를 추가·삭제할 수 있다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "수평선" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));

    expect(await screen.findByText(/수평선 @/)).toBeInTheDocument();
    expect(screen.getByText("그리기 (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(screen.queryByText(/수평선 @/)).not.toBeInTheDocument();
    expect(screen.getByText("그리기 (0)")).toBeInTheDocument();
  });

  it("negative: 그리기 도구를 고르지 않으면 추가 버튼이 비활성화된다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    expect(screen.getByRole("button", { name: "그리기 추가" })).toBeDisabled();
  });

  it("CH-3 지표 선택기로 지표를 고르면 선택 칩이 나타난다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    fireEvent.click(await screen.findByRole("option", { name: /^SMA/ }));

    expect(await screen.findByRole("button", { name: "SMA ✕" })).toBeInTheDocument();
  });

  // DC-18b 실패주입: coverageQuery는 candles 조회와 독립적으로 실행된다(주석
  // "candles 조회 실패와 독립적으로 항상 시도한다") — 그 역방향, 즉 coverage
  // 조회가 실패해도 candles 렌더링이 물려서 죽지 않는지는 지금껏 테스트가
  // 없었다. fetchCoverage가 reject하면 coverageQuery.data는 undefined로
  // 남고 CoverageBadge는 `coverageQuery.data ?? []`로 빈 배열을 받아 "미커버
  // 구간"으로 보여야 한다(에러를 화면 밖으로 던지거나 크래시하지 않음).
  it("negative: fetchCoverage가 reject해도 화면이 죽지 않고 캔들은 정상 렌더되며 커버리지는 미커버로 표시된다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const fetchCoverage = vi.fn(async () => {
      throw new Error("coverage endpoint 500");
    });
    renderPage(fetchCandles, "BTCUSDT", fakeChartingPort(), undefined, fetchCoverage);

    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    await waitFor(() => expect(fetchCoverage).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("coverage-gap-badge")).toHaveTextContent("미커버 구간"));
  });
});

// DEPTH_CH(task-2729) 감사: task-1559(02a788dc)는 fetchCandles가 항상 성공하는
// mock만 썼다 — CoverageBadge용 fetchCoverage reject(dc-18b)와 달리, 화면의 주
// 데이터 소스인 candles 조회 자체가 reject할 때의 경로(ErrorMessage 분기, 재시도
// 가능 여부 판정)는 실제로 실패를 주입해 검증한 적이 없었다. task-2928(IndicatorPicker
// DEEPEN)과 동일한 형식으로 이 leaf(ChartPage/ChartToolbar/IndicatorPicker)에 보강한다.
describe("ChartPage — CH-6a fetchCandles 실패 주입 + 재시도 판정(DEEPEN task-3077)", () => {
  it("negative/failure-injection: fetchCandles가 429(RATE_LIMIT_EXCEEDED)로 reject하면 재시도 버튼이 뜨고, 누르면 회복한다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    fetchCandles.mockRejectedValueOnce(apiErrorLike(429, "RATE_LIMIT_EXCEEDED"));
    renderPage(fetchCandles);

    expect(await screen.findByRole("button", { name: "다시 시도" })).toBeInTheDocument();
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    expect(fetchCandles).toHaveBeenCalledTimes(2);
  });

  // 게이트 적색 재현: ChartPage의 canRetry는 routeApiError(err).kind가
  // refetch_retry/backoff_retry일 때만 true다(ChartPage.tsx). "ApiError면 무조건
  // 재시도 가능"이라는 naive 판정으로 되돌리면 403 정책거부(POLICY_LIVE_BLOCKED)에도
  // 재시도 버튼이 잘못 뜬다 — 아래는 그 회귀가 실제로 이 함수 대조로 적발됨을
  // 고정하고, 뒤이어 실 컴포넌트가 naive 쪽이 아니라 real 쪽처럼 동작함을 DOM으로 증명한다.
  function realCanRetry(err: unknown): boolean {
    const routed = routeApiError(err);
    return routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
  }
  function naiveCanRetry(err: unknown): boolean {
    return err instanceof ApiError;
  }

  it("negative: fetchCandles가 403(POLICY_LIVE_BLOCKED)로 reject하면 재시도 버튼 없이 에러만 보여준다(게이트 적색 재현)", async () => {
    const policyErr = apiErrorLike(403, "POLICY_LIVE_BLOCKED");
    // naive 판정이면 여기서 이미 true가 되어 실제 회귀를 놓친다 — real은 false.
    expect(naiveCanRetry(policyErr)).toBe(true);
    expect(realCanRetry(policyErr)).toBe(false);

    const fetchCandles = vi.fn(async () => {
      throw policyErr;
    });
    renderPage(fetchCandles);

    expect(await screen.findByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });
});

// 수치 성능 단언(DEEPEN task-3077): VISIBLE_CANDLE_COUNT(200)개 캔들의 초기 fetch
// 완료→캔들스틱 표시까지 걸리는 실측 시간에 상한을 둔다 — ms/fps 절대치가 아니라
// jsdom 유닛테스트 환경에서 걸리는 벽시계 시간이라 느슨한 예산(2s)이지만, 회귀로
// 무한루프나 과도한 재렌더가 생기면(예: query key가 매 렌더 새 객체라 폴링 루프에
// 빠지는 등) 이 상한을 확실히 넘어 실패한다.
describe("ChartPage — 성능 단언(DEEPEN task-3077)", () => {
  it(`VISIBLE_CANDLE_COUNT(${VISIBLE_CANDLE_COUNT})개 캔들 초기 렌더가 2초 안에 끝난다`, async () => {
    const candles = manyCandles(VISIBLE_CANDLE_COUNT);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));

    const startedAt = performance.now();
    renderPage(fetchCandles);
    await waitFor(() =>
      expect(screen.getByTestId("candlestick-chart")).toHaveTextContent(`캔들 ${VISIBLE_CANDLE_COUNT}개`),
    );
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(2000));
  });
});

// DEPTH_CH(task-2729) 감사: task-1914(b19b7a4, CH-14·CH-16 화면 배선)는 candles
// 조회 실패 시 ChartPanes/ChartLegend/DataWindowPanel(멀티페인·objectTree·데이터
// 윈도우 묶음)이 마운트되지 않는다는 것을 실제로 실패를 주입해 검증한 적이
// 없었다 — task-3077(CH-6a)의 실패 주입은 ErrorMessage/재시도 버튼만 확인했다.
// 여기서는 같은 mockRejectedValue 관용으로 CH-14·CH-16 화면 배선 자체가
// fail-closed임을(부분/잔존 패널 노출 없음) 못박는다.
describe("ChartPage — CH-14·CH-16 멀티페인 실패 주입(DEEPEN task-3092)", () => {
  it("negative/failure-injection: fetchCandles가 실패하면 ChartPanes·ChartLegend·DataWindowPanel 전체가 마운트되지 않는다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw apiErrorLike(429, "RATE_LIMIT_EXCEEDED");
    });
    renderPage(fetchCandles);

    expect(await screen.findByRole("button", { name: "다시 시도" })).toBeInTheDocument();
    expect(screen.queryByTestId("chart-panes")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chart-legend")).not.toBeInTheDocument();
    expect(screen.queryByTestId("data-window-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
  });
});
