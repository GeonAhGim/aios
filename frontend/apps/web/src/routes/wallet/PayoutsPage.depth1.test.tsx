import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PayoutsPage, type FetchHoldsPage, type FetchPayoutBatchesPage } from "./PayoutsPage";
import { perfBudgetMs } from "../../test/perfBudget";

// ── Mocks (same pattern as PayoutsPage.test.tsx) ──────────────────────────
vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  apiClient: { markPayoutPaid: vi.fn() },
}));

afterEach(cleanup);

function meta() {
  return {
    trace_id: "trace-1",
    as_of: "2026-09-03T00:00:00Z",
    page: { total: null, page: null, size: 20, next_cursor: null },
  };
}

function renderPage({
  fetchHolds,
  fetchPayoutBatches,
}: { fetchHolds?: FetchHoldsPage; fetchPayoutBatches?: FetchPayoutBatchesPage } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PayoutsPage
          fetchHolds={fetchHolds}
          fetchPayoutBatches={fetchPayoutBatches}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── Negative tests (3+ distinct assertion types) ──────────────────────────
describe("PayoutsPage depth1 negatives", () => {
  it("fetchHolds가 reject하면 ErrorMessage가 나타난다 (getByText)", async () => {
    const errorFetch: FetchHoldsPage = () =>
      Promise.reject(new Error("network failure"));

    renderPage({ fetchHolds: errorFetch });

    await waitFor(() => {
      // ErrorMessage는 <Alert tone="danger"><p>{text}</p></Alert> 구조
      expect(screen.getByText(/network failure/)).toBeInTheDocument();
    });
  });

  it("빈 목록일 때 hold-card가 나타나지 않는다 (not.toBeInTheDocument)", async () => {
    renderPage({
      fetchHolds: async () => ({ items: [], meta: meta() }),
      fetchPayoutBatches: async () => ({ items: [], meta: meta() }),
    });

    await waitFor(() => expect(screen.queryByTestId("hold-card")).not.toBeInTheDocument());
    expect(screen.queryByTestId("payout-batch-card")).not.toBeInTheDocument();
  });

  it("fetchPayoutBatches가 reject하면 ErrorMessage가 나타난다 (getByText)", async () => {
    const errorFetch: FetchPayoutBatchesPage = () =>
      Promise.reject(new Error("정산 배치 API 오류"));

    renderPage({ fetchPayoutBatches: errorFetch });

    await waitFor(() => {
      expect(screen.getByText(/정산 배치 API 오류/)).toBeInTheDocument();
    });
  });
});

// ── Failure injection ─────────────────────────────────────────────────────
describe("PayoutsPage failure injection", () => {
  it("TypeError reject로 네트워크 오류를 주입하면 ErrorMessage가 나타난다", async () => {
    // TypeError를 던져 네트워크 오류 시뮬레이션 (HttpResponse.error 대안)
    const networkFail: FetchHoldsPage = () =>
      Promise.reject(new TypeError("Failed to fetch"));

    renderPage({ fetchHolds: networkFail });

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch/)).toBeInTheDocument();
      expect(screen.queryByTestId("hold-card")).not.toBeInTheDocument();
    });
  });
});

// ── Performance assertion ─────────────────────────────────────────────────
describe("PayoutsPage performance", () => {
  it(
    "[perf] 홀드 30건 + 정산 배치 30건 렌더 시간이 예산 안에 끝난다",
    async () => {
      const holdItems = Array.from({ length: 30 }, (_, i) => ({
        hold_id: `h-${i}`,
        account_code: `USER:u-${i}:HELD`,
        amount: "1000.00",
        purpose: "purchase",
        reference: `ref:${i}`,
        state: "PENDING",
        expires_at: "2026-09-10T00:00:00Z",
        entry_id: `e-${i}`,
        schema_version: "v1",
      }));

      const batchItems = Array.from({ length: 30 }, (_, i) => ({
        batch_id: `b-${i}`,
        seller_user_id: `u-${i}`,
        period_start: "2026-08-01",
        period_end: "2026-08-31",
        amount: "5000.00",
        state: "SCHEDULED",
        capture_entry_ids: [],
        release_entry_id: null,
        paid_entry_id: null,
        schema_version: "v1",
      }));

      const startedAt = performance.now();
      renderPage({
        fetchHolds: async () => ({ items: holdItems, meta: meta() }),
        fetchPayoutBatches: async () => ({ items: batchItems, meta: meta() }),
      });

      await waitFor(() =>
        expect(screen.getAllByTestId("hold-card")).toHaveLength(30),
      );
      const elapsedMs = performance.now() - startedAt;

      // 실측: jsdom에서 홀드 30+배치 30 약 80-150ms. 예산=실측의 30배 이상.
      // coverage 모드에서는 perfBudgetMs가 3배로 자동 확장.
      expect(elapsedMs).toBeLessThan(perfBudgetMs(6000));
    },
    20000,
  );
});

// ── Gate red reproduction ─────────────────────────────────────────────────
describe("PayoutsPage gate red", () => {
  it("gate red: fetchHolds가 reject하면 ErrorMessage가 나타나야 하는데 에러 처리가 깨지면 단언이 실패한다", async () => {
    // 게이트 적색 재현: fetchHolds가 reject하면 에러 경로가 타야 함.
    // 만약 ErrorMessage 렌더가 깨지면 이 단언이 실제로 실패한다.
    const errorFetch: FetchHoldsPage = () =>
      Promise.reject(new Error("gate-red-simulated-failure"));

    renderPage({ fetchHolds: errorFetch });

    // 에러 처리가 정상일 때만 통과. 에러 처리가 깨지면 getByText가 실패.
    await waitFor(() => {
      expect(screen.getByText(/gate-red-simulated-failure/)).toBeInTheDocument();
    });
  });

  it("gate red: fetchPayoutBatches가 즉시 reject하면 ErrorMessage가 나타나야 하는데 에러 처리가 깨지면 단언이 실패한다", async () => {
    // 게이트 적색 재현: fetchPayoutBatches가 즉시 reject하면 에러 경로가 타야 함.
    const instantReject: FetchPayoutBatchesPage = () =>
      Promise.reject(new Error("gate-red-payout-batch-failure"));

    renderPage({ fetchPayoutBatches: instantReject });

    // 에러 처리가 정상일 때만 통과
    await waitFor(() => {
      expect(screen.getByText(/gate-red-payout-batch-failure/)).toBeInTheDocument();
    }, { timeout: 5000 });
  });
});
