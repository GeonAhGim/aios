import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiResponseMeta } from "@aios/api-client";
import { PayoutsPage, type FetchHoldsPage, type FetchPayoutBatchesPage } from "./PayoutsPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(cleanup);

function hold(overrides: Record<string, unknown> = {}) {
  return {
    hold_id: "h-1",
    account_code: "USER:u-1:HELD",
    amount: "1000.00",
    purpose: "purchase",
    reference: "purchase:123",
    state: "PENDING",
    expires_at: "2026-09-10T00:00:00Z",
    entry_id: "e-1",
    schema_version: "v1",
    ...overrides,
  };
}

function payoutBatch(overrides: Record<string, unknown> = {}) {
  return {
    batch_id: "b-1",
    seller_user_id: "u-2",
    period_start: "2026-08-01",
    period_end: "2026-08-31",
    amount: "5000.00",
    state: "SCHEDULED",
    capture_entry_ids: ["e-2", "e-3"],
    release_entry_id: null,
    paid_entry_id: null,
    schema_version: "v1",
    ...overrides,
  };
}

function meta(overrides: Partial<ApiResponseMeta> = {}): ApiResponseMeta {
  return {
    trace_id: "trace-1",
    as_of: "2026-09-03T00:00:00Z",
    page: { total: null, page: null, size: 20, next_cursor: null },
    ...overrides,
  };
}

const noHolds: FetchHoldsPage = async () => ({ items: [], meta: meta() });
const noPayoutBatches: FetchPayoutBatchesPage = async () => ({ items: [], meta: meta() });

function renderPage(opts: { fetchHolds?: FetchHoldsPage; fetchPayoutBatches?: FetchPayoutBatchesPage; now?: Date }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PayoutsPage
          fetchHolds={opts.fetchHolds ?? noHolds}
          fetchPayoutBatches={opts.fetchPayoutBatches ?? noPayoutBatches}
          now={opts.now}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PayoutsPage", () => {
  it("홀드·정산 배치 목록을 각각 정상 표시한다", async () => {
    renderPage({
      fetchHolds: async () => ({ items: [hold()], meta: meta() }),
      fetchPayoutBatches: async () => ({ items: [payoutBatch()], meta: meta() }),
    });

    await waitFor(() => expect(screen.getByTestId("hold-card")).toBeInTheDocument());
    expect(screen.getByText(/purchase:123/)).toBeInTheDocument();
    expect(screen.getByTestId("payout-batch-card")).toBeInTheDocument();
    expect(screen.getByText(/2026-08-01/)).toBeInTheDocument();
  });

  it("홀드 상태와 정산 배치 상태를 서버 값 그대로 배지로 표기한다", async () => {
    renderPage({
      fetchHolds: async () => ({ items: [hold({ state: "EXPIRED" })], meta: meta() }),
      fetchPayoutBatches: async () => ({ items: [payoutBatch({ state: "PAID", release_entry_id: "e-4", paid_entry_id: "e-5" })], meta: meta() }),
    });

    await waitFor(() => expect(screen.getByTestId("hold-state-badge")).toHaveTextContent("만료"));
    expect(screen.getByTestId("payout-batch-state-badge")).toHaveTextContent("지급 완료");
  });

  it("홀드 목록에서 다음/이전 커서로 이동한다", async () => {
    const fetchHolds = vi.fn(async (cursor: string | undefined) => {
      if (cursor === undefined) {
        return { items: [hold({ hold_id: "h-1", reference: "purchase:1" })], meta: meta({ page: { total: null, page: null, size: 20, next_cursor: "cur-2" } }) };
      }
      expect(cursor).toBe("cur-2");
      return { items: [hold({ hold_id: "h-2", reference: "purchase:2" })], meta: meta() };
    });
    renderPage({ fetchHolds });

    await waitFor(() => expect(screen.getByText(/purchase:1/)).toBeInTheDocument());
    fireEvent.click(within(screen.getByTestId("holds-section")).getByRole("button", { name: "다음" }));
    await waitFor(() => expect(screen.getByText(/purchase:2/)).toBeInTheDocument());
    expect(screen.queryByText(/purchase:1/)).not.toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("holds-section")).getByRole("button", { name: "이전" }));
    await waitFor(() => expect(screen.getByText(/purchase:1/)).toBeInTheDocument());
  });

  it("금액을 소수점 8자리까지 문자열 그대로 무손실 표시한다", async () => {
    renderPage({
      fetchHolds: async () => ({ items: [hold({ amount: "12345678.12345678" })], meta: meta() }),
      fetchPayoutBatches: async () => ({ items: [payoutBatch({ amount: "0.00000001" })], meta: meta() }),
    });

    await waitFor(() => expect(screen.getByText("12345678.12345678")).toBeInTheDocument());
    expect(screen.getByText("0.00000001")).toBeInTheDocument();
  });

  it("파싱 실패 항목은 조용히 숨기지 않고 사유와 함께 노출한다", async () => {
    const { hold_id: _drop, ...malformedHold } = hold();
    renderPage({
      fetchHolds: async () => ({ items: [malformedHold], meta: meta() }),
      fetchPayoutBatches: async () => ({ items: [payoutBatch({ schema_version: "v2" })], meta: meta() }),
    });

    await waitFor(() => expect(screen.getByText("홀드 정보를 해석할 수 없습니다.")).toBeInTheDocument());
    expect(screen.getByText(/지원하지 않는 schema_version입니다 \(v2\)/)).toBeInTheDocument();
  });
});
