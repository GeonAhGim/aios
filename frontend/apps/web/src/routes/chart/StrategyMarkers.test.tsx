import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@aios/api-client";
import type { CandlestickPoint } from "@aios/ui-web";
import type { PositionsClientLike } from "../../hooks/usePositions";
import { StrategyMarkers } from "./StrategyMarkers";

const INSTRUMENT_ID = "instr-1";
const POSITION_KEY = "bitget:instr-1:strat-1:exec-1";

let executionsData: Array<{ executionId: number; strategyId: string; status: string }> = [
  { executionId: 1, strategyId: "grid-v1", status: "RUNNING" },
];

vi.mock("@aios/shared-hooks", () => ({
  useExecutions: () => ({ data: executionsData }),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(() => {
  cleanup();
  executionsData = [{ executionId: 1, strategyId: "grid-v1", status: "RUNNING" }];
});

// 2026-09-06T00:00:00Z ~ 01:00:00Z 사이 캔들 2개 — 저널 occurred_at을 이 범위 안에 둔다.
const T0 = Math.floor(Date.parse("2026-09-06T00:00:00Z") / 1000);
const T1 = T0 + 3600;
const POINTS: CandlestickPoint[] = [
  { time: T0, open: 100, high: 110, low: 90, close: 100 },
  { time: T1, open: 100, high: 120, low: 95, close: 105 },
];

function okPosition() {
  return {
    kind: "ok" as const,
    value: {
      position_key: POSITION_KEY,
      tenant_id: "t-1",
      account_id: "a-1",
      instrument_id: INSTRUMENT_ID,
      quantity: "1.5",
      avg_cost: { amount: "100.00", currency: "USDT" },
      cost_method: "FIFO" as const,
      lots: [],
      realized_pnl_base: "0",
      unrealized_pnl_base: null,
      fees_base: "0",
      funding_base: "0",
      mark_price: null,
      mark_at: null,
      base_currency: "USDT" as const,
      last_journal_seq: 3,
      updated_at: "2026-09-06T00:30:00Z",
    },
  };
}

function fillEntry(seq: number, qtyDelta: string, price: string, occurredAt: string) {
  return {
    id: seq,
    position_key: POSITION_KEY,
    sequence_no: seq,
    entry_type: "FILL",
    qty_delta: qtyDelta,
    price: { amount: price, currency: "USDT" },
    fee: null,
    realized_pnl_base: "0",
    occurred_at: occurredAt,
    recorded_at: occurredAt,
  };
}

function fundingEntry(seq: number, occurredAt: string) {
  return {
    id: seq,
    position_key: POSITION_KEY,
    sequence_no: seq,
    entry_type: "FUNDING",
    qty_delta: "0",
    price: null,
    fee: null,
    realized_pnl_base: "-1.5",
    occurred_at: occurredAt,
    recorded_at: occurredAt,
  };
}

interface RenderOpts {
  listPositions: PositionsClientLike["listPositions"];
  getPositionJournal: PositionsClientLike["getPositionJournal"];
}

function renderMarkers({ listPositions, getPositionJournal }: RenderOpts) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyMarkers
        instrumentId={INSTRUMENT_ID}
        points={POINTS}
        client={{ listPositions, getPositionJournal, getNavSeries: vi.fn() }}
      />
    </QueryClientProvider>,
  );
}

async function selectExecution() {
  fireEvent.change(screen.getByTestId("strategy-markers-execution-select"), { target: { value: "1" } });
  await screen.findByText("RUNNING");
}

describe("StrategyMarkers", () => {
  it("실행을 선택하기 전에는 마커 대신 안내 문구와 실행 상태 배지만 보여준다", async () => {
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal: vi.fn(),
    });

    expect(await screen.findByText("실행을 선택하면 신호·체결 마커를 표시합니다.")).toBeInTheDocument();
    expect(screen.queryByText("RUNNING")).not.toBeInTheDocument();
  });

  it("실행 선택 후 매수/매도/펀딩 항목을 캔들 범위 안 마커로 방향별 aria-label과 함께 그린다", async () => {
    const getPositionJournal = vi.fn().mockResolvedValue({
      positionKey: POSITION_KEY,
      items: [
        fillEntry(1, "0.50", "100.00", "2026-09-06T00:10:00Z"),
        fillEntry(2, "-0.20", "101.00", "2026-09-06T00:20:00Z"),
        fundingEntry(3, "2026-09-06T00:30:00Z"),
      ],
      nextCursor: null,
      asOf: null,
    });
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal,
    });

    await selectExecution();

    expect(await screen.findByLabelText("매수 0.50 @ 100.00")).toBeInTheDocument();
    expect(screen.getByLabelText("매도 0.20 @ 101.00")).toBeInTheDocument();
    expect(screen.getByLabelText("펀딩비 정산")).toBeInTheDocument();
    expect(getPositionJournal).toHaveBeenCalledWith({ positionKey: POSITION_KEY, cursor: undefined, limit: 200 });
  });

  it("저널이 비어 있으면 빈 문구를 보여준다", async () => {
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal: vi.fn().mockResolvedValue({ positionKey: POSITION_KEY, items: [], nextCursor: null, asOf: null }),
    });

    await selectExecution();

    expect(await screen.findByText("표시할 체결·신호가 없습니다.")).toBeInTheDocument();
  });

  it("차트 instrumentId와 일치하는 포지션이 없으면 저널을 요청하지 않고 안내만 보여준다", async () => {
    const getPositionJournal = vi.fn();
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [], asOf: null }),
      getPositionJournal,
    });

    await selectExecution();

    expect(await screen.findByText("이 심볼에 대한 포지션이 없습니다.")).toBeInTheDocument();
    expect(getPositionJournal).not.toHaveBeenCalled();
  });

  it("negative: 404 RESOURCE_NOT_FOUND는 NotFoundState로 그리고 재시도 버튼을 두지 않는다", async () => {
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal: vi.fn().mockRejectedValue(new ApiError(404, "raw", "trace-1", "RESOURCE_NOT_FOUND")),
    });

    await selectExecution();

    expect(await screen.findByText("이 포지션의 저널을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("negative: 403 AUTH_TENANT_MISMATCH는 ForbiddenNotice로 그린다", async () => {
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal: vi.fn().mockRejectedValue(new ApiError(403, "raw", "trace-2", "AUTH_TENANT_MISMATCH")),
    });

    await selectExecution();

    expect(await screen.findByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument();
  });

  it("다음/이전 버튼은 useCursorPage로 next_cursor 문자열을 그대로 실어 보낸다", async () => {
    const getPositionJournal = vi
      .fn()
      .mockImplementation(({ cursor }: { cursor?: string }) =>
        Promise.resolve(
          cursor === "0002"
            ? { positionKey: POSITION_KEY, items: [], nextCursor: null, asOf: null }
            : {
                positionKey: POSITION_KEY,
                items: [fillEntry(1, "0.50", "100.00", "2026-09-06T00:10:00Z")],
                nextCursor: "0002",
                asOf: null,
              },
        ),
      );
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal,
    });

    await selectExecution();
    await screen.findByLabelText("매수 0.50 @ 100.00");

    const next = screen.getByTestId("strategy-markers-next");
    await waitFor(() => expect(next).not.toBeDisabled());
    fireEvent.click(next);

    await waitFor(() =>
      expect(getPositionJournal).toHaveBeenLastCalledWith({ positionKey: POSITION_KEY, cursor: "0002", limit: 200 }),
    );
  });

  // failure-injection: 단순 mockRejectedValue 1회가 아니라 "일시 장애(503) → api-client
  // 재시도 정책(backoff_retry, PortfolioPage.errors.test.tsx와 동일 관용)의 카운트다운
  // 소진 → 재시도 버튼 클릭 → 실제 성공 응답"까지 전체 복구 경로를 통과시킨다. 이 리프의
  // negative 3번째이기도 하다(§DEPTH_LA_LB_LC D2 하한: negative≥3).
  it("negative: 503 EXCHANGE_UNAVAILABLE은 재시도 카운트다운 뒤 실제 재요청이 성공하면 마커로 복구된다", async () => {
    const getPositionJournal = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, "raw", "trace-3", "EXCHANGE_UNAVAILABLE", 2))
      .mockResolvedValueOnce({
        positionKey: POSITION_KEY,
        items: [fillEntry(1, "0.50", "100.00", "2026-09-06T00:10:00Z")],
        nextCursor: null,
        asOf: null,
      });
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal,
    });

    await selectExecution();

    // ErrorMessage 내부 카운트다운은 쿼리 거부(마이크로태스크)가 실제로 해소된 뒤에야
    // 마운트되는 컴포넌트-로컬 setTimeout 체인이다 — 그 마운트 시점 이전에 가짜
    // 타이머로 전환하면 두 번째 tick(1→0)이 재예약되는 타이밍을 가짜 타이머 루프가
    // 놓쳐 카운트다운이 1초에서 영구히 멈춘다(1차 시도에서 실측). 실제 시계로
    // 2초를 그대로 흘려보내 이 경합을 피한다.
    expect(await screen.findByText("2초 후 재시도 가능")).toBeInTheDocument();
    const retryButton = screen.getByRole("button", { name: "다시 시도" });
    expect(retryButton).toBeDisabled();
    expect(getPositionJournal).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(retryButton).toBeEnabled(), { timeout: 5000 });
    expect(screen.queryByText(/초 후 재시도 가능/)).not.toBeInTheDocument();

    fireEvent.click(retryButton);

    expect(await screen.findByLabelText("매수 0.50 @ 100.00")).toBeInTheDocument();
    expect(getPositionJournal).toHaveBeenCalledTimes(2);
  });

  it(
    "수치/성능: 페이지 상한(200)만큼의 저널 항목을 받아도 정확히 200개 마커를 여유 시간 안에 그린다",
    async () => {
      const ITEM_COUNT = 200;
      const stepSec = Math.floor((T1 - T0) / ITEM_COUNT);
      const items = Array.from({ length: ITEM_COUNT }, (_, i) =>
        fillEntry(
          i + 1,
          i % 2 === 0 ? "0.10" : "-0.10",
          "100.00",
          new Date((T0 + i * stepSec) * 1000).toISOString(),
        ),
      );
      const getPositionJournal = vi
        .fn()
        .mockResolvedValue({ positionKey: POSITION_KEY, items, nextCursor: null, asOf: null });

      const startedAt = performance.now();
      renderMarkers({
        listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
        getPositionJournal,
      });
      await selectExecution();
      await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(ITEM_COUNT), { timeout: 15000 });
      const elapsedMs = performance.now() - startedAt;

      // jsdom 렌더 자체가 무겁고 공유 머신에서 실측 편차가 커서(PortfolioPage.test.tsx의
      // 40장 카드 케이스와 동일 사유) 절대 임계값을 넉넉히 둔다 — 마커 1개당 O(1) 매핑이
      // 저널 전체를 마커마다 재스캔하는 O(n^2)로 퇴행하면 200건도 이 임계값을 넘긴다.
      expect(elapsedMs).toBeLessThan(15000);
    },
    20000,
  );

  // 게이트 적색 재현: getPositionJournal이 403을 화면까지 던지지 않고 클라이언트
  // 계층에서 삼켜 빈 목록으로 응답하는 회귀(예: 캐시/재시도 래퍼가 catch를 잘못 얹는
  // 사고)를 상정한다. "negative: 403 AUTH_TENANT_MISMATCH는 ForbiddenNotice로 그린다"가
  // 이 회귀를 실제로 적색으로 잡아낼 단언(ForbiddenNotice 문구)을 갖고 있다는 것을
  // 대조군으로 증명한다 — 이 회귀 아래에서는 그 문구가 사라지고 빈 목록 문구로
  // 바뀐다(marketData DEPTH DEEPEN, b45d6e56/f0c5958과 동일 기법).
  it("게이트 적색 재현: 403을 삼켜 빈 목록으로 응답하는 회귀는 ForbiddenNotice 문구를 지운다(대조군)", async () => {
    const swallowing403 = vi.fn().mockImplementation(async () => {
      try {
        throw new ApiError(403, "raw", "trace-4", "AUTH_TENANT_MISMATCH");
      } catch {
        return { positionKey: POSITION_KEY, items: [], nextCursor: null, asOf: null };
      }
    });
    renderMarkers({
      listPositions: vi.fn().mockResolvedValue({ items: [okPosition()], asOf: null }),
      getPositionJournal: swallowing403,
    });

    await selectExecution();

    expect(await screen.findByText("표시할 체결·신호가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("이 리소스에 접근할 권한이 없습니다.")).not.toBeInTheDocument();
  });
});
