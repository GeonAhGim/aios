import "@testing-library/jest-dom/vitest";
import { ApiError, type CandleQueryResult } from "@aios/api-client";
import { createEmptyLayoutModel, encodeLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import type { ChartingLayoutRecord, ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartPage, type ChartPageProps, type FetchCandles } from "./ChartPage";

// ChartPage는 restore/save 에러를 ApiError instanceof로 판별해 errorCode/traceId를
// 뽑는다(query.error와 동일 관용) — 던지는 값이 실제 ApiError 인스턴스여야 한다.
function apiErrorLike(statusCode: number, errorCode: string): ApiError {
  return new ApiError(statusCode, errorCode, undefined, errorCode);
}

function fakeChartingPort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(async () => []),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

function layoutRecordFor(instrumentId: string, overrides: Partial<ChartingLayoutRecord> = {}): ChartingLayoutRecord {
  const panel = {
    id: "server-panel",
    instrument: { instrumentId, venue: "BITGET", symbol: instrumentId },
    timeframe: "1h",
    indicators: [],
    drawingSetId: "server-panel",
  };
  const model = { ...createEmptyLayoutModel(), panels: [panel], activePanelId: panel.id };
  return {
    id: "layout-1",
    name: "저장된 레이아웃",
    layoutState: encodeLayoutModel(model),
    revision: 1,
    updatedAt: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
  // StrategyMarkers(task-1569)의 실행 선택 전에는 positions.list를 요청하지 않으므로
  // 이 화면 테스트에서는 실행 목록이 비어 있어도 무방하다.
  useExecutions: () => ({ data: [] }),
  // CH-9: ChartToolbar가 마운트하는 AlertFromChart가 필요로 한다 — 이 스위트는
  // 알림 다이얼로그 자체를 검증하지 않으므로(ChartToolbar.test.tsx 몫) 최소 스텁만 준다.
  useCreateAlert: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// CandlesPage.test.tsx와 동일한 관용: lightweight-charts는 jsdom에서 canvas를
// 요구해 렌더링을 스텁으로 바꾼다(실제 렌더링은 유닛 테스트 영역이 아님).
vi.mock("@aios/ui-web", async () => {
  const actual = await vi.importActual<typeof import("@aios/ui-web")>("@aios/ui-web");
  return {
    ...actual,
    CandlestickChart: ({ data }: { data: unknown[] }) => (
      <div data-testid="candlestick-chart">캔들 {data.length}개</div>
    ),
  };
});

// IND-14(task-1915): IndicatorPicker 기본값은 createIndicatorsClient가 만드는 실제
// fetch 클라이언트다 — 이 화면 테스트가 "지표 선택" 버튼을 열 때 실제 네트워크를
// 타지 않도록 listIndicators만 스텁으로 바꾼다(ApiError 등 나머지 export는 실제 그대로).
vi.mock("@aios/api-client", async () => {
  const actual = await vi.importActual<typeof import("@aios/api-client")>("@aios/api-client");
  return {
    ...actual,
    createIndicatorsClient: () => ({
      listIndicators: vi.fn(async () => ({
        items: [
          {
            name: "SMA",
            tier: "core" as const,
            category: "Overlap Studies",
            version: "1",
            hash: "h",
            inputs: ["close"],
            outputs: ["value"],
          },
        ],
        nextCursor: null,
      })),
    }),
  };
});

afterEach(cleanup);

const KEY = { venue: "BITGET" as const, instrument_id: "BTCUSDT", timeframe: "1h" as const };

function candle(hourOffset: number) {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    key: KEY,
    open_time: open.toISOString(),
    close_time: close.toISOString(),
    open: "50000.00",
    high: "50500.00",
    low: "49800.00",
    close: "50200.00",
    volume: "12.5",
    quote_volume: "628500.00",
  };
}

function okResult(): CandleQueryResult {
  return {
    series: {
      kind: "ok",
      value: {
        key: KEY,
        candles: [candle(0), candle(1), candle(2)],
        gaps: [],
        adjustment: "RAW",
        as_of: new Date(Date.UTC(2026, 8, 3, 5, 0, 0)).toISOString(),
        series_hash: "deadbeef",
      },
    },
    quality: null,
  };
}

function renderPage(
  fetchCandles: FetchCandles,
  instrumentId: string | null = "BTCUSDT",
  chartingPort: ChartingPort = fakeChartingPort(),
  listInstruments?: ChartPageProps["listInstruments"],
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const entry = instrumentId === null ? "/chart" : `/chart?instrument_id=${instrumentId}`;
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ChartPage
          fetchCandles={fetchCandles}
          chartingPort={chartingPort}
          listInstruments={listInstruments}
          now={new Date("2026-09-03T05:02:00Z")}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

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
});

// CH-18b: parityCheck.ts 기반 클라이언트/서버 지표값 대조·폴백 화면 배선.
// 종가를 전부 50200으로 고정해(candle() 헬퍼) SMA(20)의 기대값을 손으로 계산할
// 필요 없이 정확히 50200으로 만든다 — timeperiod=20에 걸리도록 25개 봉을 만든다.
function manyCandles(count: number) {
  return Array.from({ length: count }, (_, i) => candle(i));
}

function okResultWithCandles(candles: ReturnType<typeof candle>[]): CandleQueryResult {
  return {
    series: {
      kind: "ok",
      value: {
        key: KEY,
        candles,
        gaps: [],
        adjustment: "RAW",
        as_of: new Date(Date.UTC(2026, 8, 4, 5, 0, 0)).toISOString(),
        series_hash: "deadbeef",
      },
    },
    quality: null,
  };
}

function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [{ name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] }];
}

function serverSmaSeries(count: number, lastValue: number) {
  return { value: Array.from({ length: count }, (_, i) => (i < 19 ? null : lastValue)) };
}

describe("ChartPage — CH-18b 클라이언트/서버 지표 패리티 폴백", () => {
  async function selectSma(fetchCandles: FetchCandles, extraProps: Partial<ChartPageProps>) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/chart?instrument_id=BTCUSDT"]}>
          <ChartPage
            fetchCandles={fetchCandles}
            chartingPort={fakeChartingPort()}
            now={new Date("2026-09-04T05:02:00Z")}
            {...extraProps}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 25개"));
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    fireEvent.click(await screen.findByRole("option", { name: /^SMA/ }));
  }

  it("서버 참조와 일치하면 클라이언트 계산값을 그대로 그린다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));
    const resolveServerIndicatorSeries = vi.fn(() => serverSmaSeries(25, 50200));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog(), resolveServerIndicatorSeries });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000000"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)");
    expect(screen.queryByTestId("indicator-parity-fallback-SMA")).not.toBeInTheDocument();
  });

  it("negative: 서버 참조와 불일치하면 서버값으로 폴백하고, 폴백 사유가 표면화되며, 클라이언트 값은 잔존하지 않는다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));
    // 클라이언트 SMA(20) 실값은 50200 — 서버가 50200.00002를 돌려주면 절대오차
    // 2e-5 > PARITY_TOLERANCE(1e-9)로 반드시 불일치가 된다(무음 통과 불가).
    const resolveServerIndicatorSeries = vi.fn(() => serverSmaSeries(25, 50200.00002));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog(), resolveServerIndicatorSeries });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000020"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    // 클라이언트가 계산했을 값(50200.000000)이 화면 어디에도 남아있지 않아야 한다.
    expect(screen.queryByText("50200.000000")).not.toBeInTheDocument();
  });

  it("서버 참조가 아직 없으면(포트 미배선) 미검증 상태로 fail-closed하고 숫자를 그리지 않는다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog() });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("--"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(unverified)");
  });
});

// CH-6b/CH-8: 서버 저장·복원 배선 — 저장 계층은 useChartLayout(→ chart-engine
// layout/persistence.ts)만 거친다(화면에서 fetch 직접 호출 금지).
describe("ChartPage — CH-8 레이아웃 저장·복원", () => {
  it("복원 → 편집(패널 추가) → 저장 왕복: 최초 저장은 createLayout, 재저장은 updateLayout으로 간다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    let saved: ChartingLayoutRecord | undefined;
    const chartingPort = fakeChartingPort({
      createLayout: vi.fn(async (input) => {
        saved = { id: "layout-1", name: input.name, layoutState: input.layoutState, revision: 0, updatedAt: "t0" };
        return saved;
      }),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    expect(await screen.findByRole("tab")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "패널 추가" }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    await waitFor(() => expect(chartingPort.createLayout).toHaveBeenCalledTimes(1));

    chartingPort.updateLayout = vi.fn(async (id, input) => ({
      id,
      name: input.name ?? saved!.name,
      layoutState: input.layoutState ?? saved!.layoutState,
      revision: (input.expectedRevision ?? 0) + 1,
      updatedAt: "t1",
    }));
    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    await waitFor(() => expect(chartingPort.updateLayout).toHaveBeenCalledWith("layout-1", expect.anything()));
    expect(chartingPort.createLayout).toHaveBeenCalledTimes(1);
  });

  it("negative: 초기 복원이 실패하면 재시도 안내가 뜨고, 재시도하면 회복한다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const chartingPort = fakeChartingPort({
      listLayouts: vi
        .fn()
        .mockRejectedValueOnce(apiErrorLike(429, "RATE_LIMIT_EXCEEDED"))
        .mockResolvedValueOnce([]),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);

    expect(await screen.findByRole("button", { name: "다시 시도" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument());
    expect(chartingPort.listLayouts).toHaveBeenCalledTimes(2);
  });
});

describe("ChartPage — CH-8 409 충돌 안내", () => {
  it("저장이 충돌하면 자동으로 덮어쓰지 않고, '다시 불러오기'를 누르면 최신 내용을 다시 가져온다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const record = layoutRecordFor("BTCUSDT");
    const chartingPort = fakeChartingPort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
      getLayout: vi.fn(async () => ({ ...record, revision: 2 })),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    expect(await screen.findByText(/다른 세션이 먼저 저장했습니다/)).toBeInTheDocument();
    expect(chartingPort.getLayout).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    await waitFor(() => expect(chartingPort.getLayout).toHaveBeenCalledWith("layout-1"));
    await waitFor(() => expect(screen.queryByText(/다른 세션이 먼저 저장했습니다/)).not.toBeInTheDocument());
  });

  // CH-13b: 비교 심볼도 CH-8 레이아웃 저장 경로(useChartLayout.save)를 그대로 타므로
  // 409는 새 분류기 없이 기존 배너로 표면화되어야 한다(decision).
  it("negative: 비교 심볼을 추가한 뒤 저장이 409로 충돌해도 같은 배너로 안내한다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const record = layoutRecordFor("BTCUSDT");
    const chartingPort = fakeChartingPort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    const listInstruments = vi.fn(async () => ({
      items: [
        {
          kind: "ok" as const,
          value: {
            instrument_id: "ETHUSDT",
            venue: "BITGET" as const,
            canonical_symbol: "ETHUSDT",
            venue_symbol: "ETHUSDT",
            asset_class: "CRYPTO" as const,
            base: null,
            quote: null,
            tick_size: "0.01",
            lot_size: "0.001",
            status: "LISTED" as const,
            listed_at: "2020-01-01T00:00:00Z",
            delisted_at: null,
          },
        },
      ],
      nextCursor: null,
    }));
    renderPage(fetchCandles, "BTCUSDT", chartingPort, listInstruments);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "비교 심볼 추가" }));
    const instrumentSelect = await screen.findByLabelText("비교 심볼 선택");
    await waitFor(() => expect((instrumentSelect as HTMLSelectElement).options.length).toBeGreaterThan(1));
    fireEvent.change(instrumentSelect, { target: { value: "ETHUSDT" } });
    fireEvent.click(screen.getByRole("button", { name: "추가" }));
    expect(await screen.findByTestId("compare-pane-ETHUSDT")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    expect(await screen.findByText(/다른 세션이 먼저 저장했습니다/)).toBeInTheDocument();
    expect(chartingPort.updateLayout).toHaveBeenCalledWith(
      "layout-1",
      expect.objectContaining({
        layoutState: expect.objectContaining({
          panels: expect.arrayContaining([
            expect.objectContaining({ indicators: expect.arrayContaining([{ id: "compare:BITGET:ETHUSDT" }]) }),
          ]),
        }),
      }),
    );
  });
});

// CH-4b(task-2012): 그리기 서버 영속화 실배선. useChartLayout과 동일하게 "레이아웃
// 저장" 버튼이 layoutId를 확보한 뒤 곧바로 그 도형 세트를 PUT한다(chartToolbarLayoutProps.ts).
describe("ChartPage — CH-4b 그리기 저장·복원", () => {
  function drawingsDocRecord(layoutId: string, schemaVersion: number, drawings: readonly unknown[]) {
    return { layoutId, document: { schema_version: schemaVersion, drawings }, revision: 1, updatedAt: "t1" };
  }

  it("도형 3종(추세선·수평선·피보나치)을 그리고 저장하면 putDrawings가 실제로 호출되고(실배선), 컴포넌트를 재마운트하면 그 페이로드 그대로 좌표·스타일까지 동일하게 복원된다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    let stored: { schemaVersion: number; drawings: unknown[] } | undefined;
    const chartingPort = fakeChartingPort({
      createLayout: vi.fn(async (input) => ({
        id: "layout-1",
        name: input.name,
        layoutState: input.layoutState,
        revision: 0,
        updatedAt: "t0",
      })),
      putDrawings: vi.fn(async (layoutId, input) => {
        stored = { schemaVersion: input.schemaVersion, drawings: [...input.drawings] };
        return drawingsDocRecord(layoutId, input.schemaVersion, input.drawings);
      }),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "추세선" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "수평선" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "피보나치" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));
    expect(screen.getByText("그리기 (3)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    await waitFor(() => expect(chartingPort.putDrawings).toHaveBeenCalledTimes(1));
    expect(chartingPort.putDrawings).toHaveBeenCalledWith(
      "layout-1",
      expect.objectContaining({ expectedRevision: 0 }),
    );
    expect(stored?.drawings).toHaveLength(3);

    cleanup();

    // 재마운트: 방금 putDrawings가 실제로 받은 페이로드(수기 값이 아니다 — 동어반복
    // 목 금지)를 getDrawings 응답으로 그대로 되돌린다.
    const remountPort = fakeChartingPort({
      listLayouts: vi.fn(async () => [layoutRecordFor("BTCUSDT", { id: "layout-1", revision: 0 })]),
      getDrawings: vi.fn(async () => drawingsDocRecord("layout-1", stored!.schemaVersion, stored!.drawings)),
    });
    renderPage(fetchCandles, "BTCUSDT", remountPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    await waitFor(() => expect(screen.getByText("그리기 (3)")).toBeInTheDocument());
    expect(screen.getByText(/^추세선 \(/)).toBeInTheDocument();
    expect(screen.getByText(/^수평선 @/)).toBeInTheDocument();
    expect(screen.getByText(/^피보나치 \(/)).toBeInTheDocument();
  });

  it("negative: 저장이 409로 충돌해도 로컬 도형을 지우지 않고 배너로 안내한다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const chartingPort = fakeChartingPort({
      createLayout: vi.fn(async (input) => ({
        id: "layout-1",
        name: input.name,
        layoutState: input.layoutState,
        revision: 0,
        updatedAt: "t0",
      })),
      putDrawings: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "수평선" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));
    expect(screen.getByText("그리기 (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "레이아웃 저장" }));
    await waitFor(() => expect(chartingPort.putDrawings).toHaveBeenCalledTimes(1));

    expect(await screen.findByText(/다른 요청과 충돌했습니다/)).toBeInTheDocument();
    // negative: 조용히 삼키거나 로컬 상태를 비우지 않는다.
    expect(screen.getByText("그리기 (1)")).toBeInTheDocument();
  });

  it("negative: 교차 테넌트·미존재 layout_id(404)는 빈 도형 목록으로 뭉개지 않고 배너로 표면화한다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    const record = layoutRecordFor("BTCUSDT");
    const chartingPort = fakeChartingPort({
      listLayouts: vi.fn(async () => [record]),
      getDrawings: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")),
    });
    renderPage(fetchCandles, "BTCUSDT", chartingPort);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    expect(await screen.findByText(/요청한 항목을 찾을 수 없습니다/)).toBeInTheDocument();
  });
});
