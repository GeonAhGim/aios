import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@aios/api-client";
import type { PositionsClientLike } from "../../hooks/usePositions";
import { PositionJournalPanel } from "./PositionJournalPanel";

vi.mock("@aios/shared-hooks", () => ({
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(() => cleanup());

const KEY = "upbit:BTC-KRW:strat-1:exec-1";

function entry(seq: number) {
  return {
    id: seq,
    position_key: KEY,
    sequence_no: seq,
    entry_type: "FILL",
    qty_delta: "0.5",
    price: { amount: "50000.00", currency: "KRW" },
    fee: null,
    realized_pnl_base: "0",
    fx_rate: null,
    fx_source: null,
    source_event_type: "fill",
    source_event_id: `f-${seq}`,
    idempotency_key: `k-${seq}`,
    prev_hash: null,
    entry_hash: `h-${seq}`,
    occurred_at: "2026-09-05T00:00:00Z",
    recorded_at: "2026-09-05T00:00:01Z",
    schema_version: "v1",
  };
}

function page(items: unknown[], nextCursor: string | null) {
  return { positionKey: KEY, items, nextCursor, asOf: "2026-09-05T12:00:00Z" };
}

function renderPanel(getPositionJournal: PositionsClientLike["getPositionJournal"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PositionJournalPanel client={{ getPositionJournal }} positionKey={KEY} pageSize={2} />
    </QueryClientProvider>,
  );
}

// task-1524(LB-19): 저널은 접힌 상태에서 요청하지 않고, 펼치면 useCursorPage로 meta.page.
// next_cursor(문자열)를 그대로 다음 요청의 cursor에 실어 보낸다 — 숫자 변환·해석 금지.
describe("PositionJournalPanel", () => {
  it("접힌 상태에서는 요청하지 않고, 펼치면 첫 페이지(cursor 없음)를 요청해 행을 그린다", async () => {
    const getPositionJournal = vi.fn().mockResolvedValue(page([entry(1), entry(2)], "0002"));
    renderPanel(getPositionJournal);

    expect(getPositionJournal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-open`));

    expect(await screen.findAllByText("50000.00 KRW")).toHaveLength(2);
    expect(getPositionJournal).toHaveBeenCalledWith({ positionKey: KEY, cursor: undefined, limit: 2 });
    expect(screen.getAllByText("FILL")).toHaveLength(2);
  });

  it("다음 페이지는 next_cursor 문자열을 그대로 cursor로 보내고, 이전은 첫 페이지로 되돌아간다", async () => {
    const getPositionJournal = vi
      .fn()
      .mockImplementation(({ cursor }: { cursor?: string }) =>
        Promise.resolve(cursor === "0002" ? page([entry(3)], null) : page([entry(1), entry(2)], "0002")),
      );
    renderPanel(getPositionJournal);
    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-open`));
    await screen.findAllByText("FILL");

    const next = screen.getByTestId(`position-journal-${KEY}-next`);
    await waitFor(() => expect(next).not.toBeDisabled());
    fireEvent.click(next);

    await waitFor(() =>
      expect(getPositionJournal).toHaveBeenLastCalledWith({ positionKey: KEY, cursor: "0002", limit: 2 }),
    );
    await screen.findByText("3");
    // 마지막 페이지(next_cursor=null): 다음 버튼 비활성.
    await waitFor(() => expect(screen.getByTestId(`position-journal-${KEY}-next`)).toBeDisabled());

    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-prev`));
    await waitFor(() => expect(screen.getAllByText("FILL")).toHaveLength(2));
  });

  it("항목이 없으면 빈 문구를 보여준다", async () => {
    renderPanel(vi.fn().mockResolvedValue(page([], null)));
    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-open`));

    expect(await screen.findByTestId(`position-journal-${KEY}-empty`)).toBeInTheDocument();
    expect(screen.getByTestId(`position-journal-${KEY}-next`)).toBeDisabled();
  });

  it("negative: 필드가 빠진 raw 항목은 예외 없이 '-'로 그린다(파서 없이 방어적 표시)", async () => {
    renderPanel(vi.fn().mockResolvedValue(page([{ sequence_no: 9 }, "garbage"], null)));
    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-open`));

    expect(await screen.findByText("9")).toBeInTheDocument();
    expect(screen.getAllByText("-").length).toBeGreaterThan(6);
  });

  it("negative: 404 RESOURCE_NOT_FOUND(타 테넌트 포함)는 NotFoundState로 그리고 재시도 버튼을 두지 않는다", async () => {
    renderPanel(vi.fn().mockRejectedValue(new ApiError(404, "raw", "trace-1", "RESOURCE_NOT_FOUND")));
    fireEvent.click(screen.getByTestId(`position-journal-${KEY}-open`));

    expect(await screen.findByText("저널이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
  });
});
