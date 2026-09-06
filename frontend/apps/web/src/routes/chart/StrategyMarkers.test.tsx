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
});
