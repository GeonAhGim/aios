import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiResponseMeta } from "@aios/api-client";
import { PayoutsPage, type FetchHoldsPage, type FetchPayoutBatchesPage, type MarkPayoutPaidFn } from "./PayoutsPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  apiClient: { markPayoutPaid: vi.fn() },
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

function renderPage(opts: {
  fetchHolds?: FetchHoldsPage;
  fetchPayoutBatches?: FetchPayoutBatchesPage;
  markPayoutPaid?: MarkPayoutPaidFn;
  now?: Date;
}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PayoutsPage
          fetchHolds={opts.fetchHolds ?? noHolds}
          fetchPayoutBatches={opts.fetchPayoutBatches ?? noPayoutBatches}
          markPayoutPaid={opts.markPayoutPaid}
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

  it("정산 배치 조회가 ApiError 봉투로 실패하면 매핑된 메시지와 지원코드를 보여준다", async () => {
    renderPage({
      fetchPayoutBatches: async () => {
        throw new ApiError(500, "server message", "trace-payout-1", "INTERNAL_ERROR");
      },
    });

    await waitFor(() =>
      expect(
        screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("지원코드: trace-payout-1")).toBeInTheDocument();
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

  // task-4026(FE-OPS-7c) 정산 배치 확정 액션. ADR-2026-09-09-C D2 증거:
  // negative >=3(아래 세 건), failure injection 1(break-glass 거부/LC-15a 상태충돌),
  // perf assertion 1(다건 렌더 예산), gate-red repro는 이 리프가 새 정적 게이트를
  // 만들지 않아 N/A(check_i18n_literals.test.mjs가 스캐너 자체의 red 재현을 전역
  // 커버, 위 npm run lint 실행이 이 파일에 대해 그 게이트 green을 이미 확인).
  describe("정산 배치 확정(markPayoutPaid)", () => {
    it("[negative] 정산 대기(RELEASED) 상태 배치에만 확정 액션을 노출한다", async () => {
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [
            payoutBatch({ batch_id: "b-released", state: "RELEASED", release_entry_id: "e-4" }),
            payoutBatch({ batch_id: "b-paid", state: "PAID", release_entry_id: "e-4", paid_entry_id: "e-5" }),
          ],
          meta: meta(),
        }),
      });

      await waitFor(() => expect(screen.getAllByTestId("payout-batch-card")).toHaveLength(2));
      expect(screen.getAllByTestId("payout-batch-mark-paid")).toHaveLength(1);
    });

    it("[negative] SCHEDULED·FAILED 상태 배치는 확정 폼을 전혀 보여주지 않는다", async () => {
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [
            payoutBatch({ batch_id: "b-scheduled", state: "SCHEDULED" }),
            payoutBatch({ batch_id: "b-failed", state: "FAILED" }),
          ],
          meta: meta(),
        }),
      });

      await waitFor(() => expect(screen.getAllByTestId("payout-batch-card")).toHaveLength(2));
      expect(screen.queryByTestId("payout-batch-mark-paid")).not.toBeInTheDocument();
    });

    it("[negative] 외부 참조번호 또는 그랜트 ID가 비어 있으면 확정 버튼이 비활성화된다", async () => {
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [payoutBatch({ batch_id: "b-released", state: "RELEASED", release_entry_id: "e-4" })],
          meta: meta(),
        }),
      });

      await waitFor(() => expect(screen.getByTestId("payout-batch-mark-paid")).toBeInTheDocument());
      const form = screen.getByTestId("payout-batch-mark-paid");
      const button = within(form).getByRole("button", { name: "정산 확정" });
      const [refInput, grantInput] = within(form).getAllByRole("textbox");
      expect(button).toBeDisabled();

      fireEvent.change(refInput, { target: { value: "ext-ref-1" } });
      expect(button).toBeDisabled();

      fireEvent.change(grantInput, { target: { value: "grant-uuid-1" } });
      expect(button).toBeEnabled();
    });

    it("외부 참조번호·그랜트 ID를 입력해 확정하면 성공 배지를 보여주고 입력 폼을 감춘다", async () => {
      const markPayoutPaid = vi.fn(async () => ({
        batchId: "b-released",
        sellerUserId: "u-2",
        periodStart: "2026-08-01",
        periodEnd: "2026-08-31",
        amount: "5000.00",
        state: "PAID" as const,
        captureEntryIds: ["e-2", "e-3"],
        releaseEntryId: "e-4",
        paidEntryId: "e-6",
      }));
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [payoutBatch({ batch_id: "b-released", state: "RELEASED", release_entry_id: "e-4" })],
          meta: meta(),
        }),
        markPayoutPaid,
      });

      await waitFor(() => expect(screen.getByTestId("payout-batch-mark-paid")).toBeInTheDocument());
      const form = screen.getByTestId("payout-batch-mark-paid");
      const [refInput, grantInput] = within(form).getAllByRole("textbox");
      fireEvent.change(refInput, { target: { value: "ext-ref-1" } });
      fireEvent.change(grantInput, { target: { value: "grant-uuid-1" } });
      fireEvent.click(within(form).getByRole("button", { name: "정산 확정" }));

      await waitFor(() =>
        expect(screen.getByText("정산 배치가 지급 완료로 확정됐습니다.")).toBeInTheDocument(),
      );
      expect(screen.queryByTestId("payout-batch-mark-paid")).not.toBeInTheDocument();
      expect(markPayoutPaid).toHaveBeenCalledWith("b-released", "ext-ref-1", "grant-uuid-1");
    });

    it("[failure-injection] break-glass 그랜트가 무효하면(403) 서버 메시지 대신 매핑된 권한 문구를 보여준다", async () => {
      const markPayoutPaid: MarkPayoutPaidFn = async () => {
        throw new ApiError(403, "grant expired", "trace-grant-1", "AUTHZ_FORBIDDEN");
      };
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [payoutBatch({ batch_id: "b-released", state: "RELEASED", release_entry_id: "e-4" })],
          meta: meta(),
        }),
        markPayoutPaid,
      });

      await waitFor(() => expect(screen.getByTestId("payout-batch-mark-paid")).toBeInTheDocument());
      const form = screen.getByTestId("payout-batch-mark-paid");
      const [refInput, grantInput] = within(form).getAllByRole("textbox");
      fireEvent.change(refInput, { target: { value: "ext-ref-1" } });
      fireEvent.change(grantInput, { target: { value: "expired-grant" } });
      fireEvent.click(within(form).getByRole("button", { name: "정산 확정" }));

      await waitFor(() =>
        expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
      );
      expect(screen.queryByText("grant expired")).not.toBeInTheDocument();
    });

    it("[failure-injection] 다른 요청이 이미 확정한 배치(LC-15a 조건부 UPDATE 충돌)를 다시 확정하면 재시도 없이 상태 오류를 보여준다", async () => {
      const markPayoutPaid: MarkPayoutPaidFn = async () => {
        throw new ApiError(409, "already paid", "trace-conflict-1", "STATE_INVALID_TRANSITION");
      };
      renderPage({
        fetchPayoutBatches: async () => ({
          items: [payoutBatch({ batch_id: "b-released", state: "RELEASED", release_entry_id: "e-4" })],
          meta: meta(),
        }),
        markPayoutPaid,
      });

      await waitFor(() => expect(screen.getByTestId("payout-batch-mark-paid")).toBeInTheDocument());
      const form = screen.getByTestId("payout-batch-mark-paid");
      const [refInput, grantInput] = within(form).getAllByRole("textbox");
      fireEvent.change(refInput, { target: { value: "ext-ref-1" } });
      fireEvent.change(grantInput, { target: { value: "grant-uuid-1" } });
      fireEvent.click(within(form).getByRole("button", { name: "정산 확정" }));

      await waitFor(() =>
        expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
      );
      expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    });

    it(
      "[perf] 정산 대기 배치 60건을 확정 폼과 함께 렌더링해도 예산 안에서 끝난다(카드별 O(1) 렌더 가드)",
      async () => {
        const items = Array.from({ length: 60 }, (_, i) =>
          payoutBatch({ batch_id: `b-${i}`, state: "RELEASED", release_entry_id: `e-${i}` }),
        );
        const startedAt = performance.now();
        renderPage({ fetchPayoutBatches: async () => ({ items, meta: meta() }) });

        await waitFor(() => expect(screen.getAllByTestId("payout-batch-mark-paid")).toHaveLength(60));
        const elapsedMs = performance.now() - startedAt;

        // PortfolioPage.test.tsx와 같은 사유(task-1968/3460): jsdom 렌더 + 공유 CI
        // 머신 경합으로 절대 임계값을 넉넉히 둔다 — 카드마다 O(1) 렌더가 카드마다
        // 전체 목록을 재스캔하는 O(n^2)로 퇴행하면 60건도 이 임계값을 넘긴다.
        expect(elapsedMs).toBeLessThan(perfBudgetMs(8000));
      },
      20000,
    );
  });
});
