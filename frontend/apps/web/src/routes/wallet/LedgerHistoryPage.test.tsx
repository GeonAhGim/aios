import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiResponseMeta } from "@aios/api-client";
import { LedgerHistoryPage, type FetchLedgerHistoryPage } from "./LedgerHistoryPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(cleanup);

const DEBIT_LINE = { line_no: 1, account_code: "USER:u-1:AVAILABLE", side: "DEBIT", amount: "1000.00", currency: "KRW" };
const CREDIT_LINE = { line_no: 2, account_code: "PLATFORM:REVENUE", side: "CREDIT", amount: "1000.00", currency: "KRW" };

function entry(overrides: Record<string, unknown> = {}) {
  return {
    entry_id: "e-1",
    sequence_no: 42,
    event_type: "TOPUP_CONFIRMED",
    event_ref: "topup:45",
    idempotency_key: "idem-1",
    lines: [DEBIT_LINE, CREDIT_LINE],
    lines_digest: "digest-1",
    prev_hash: "hash-0",
    entry_hash: "hash-1",
    audit_event_id: "audit-1",
    posted_at: "2026-09-03T00:00:00Z",
    replayed: false,
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

function renderPage(fetchPage: FetchLedgerHistoryPage, now?: Date) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LedgerHistoryPage fetchPage={fetchPage} now={now} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LedgerHistoryPage", () => {
  it("정상 목록을 보여주고 as_of 신선도를 표기한다", async () => {
    const now = new Date("2026-09-03T00:02:00Z");
    renderPage(async () => ({ entries: [entry()], meta: meta() }), now);

    await waitFor(() => expect(screen.getByText(/TOPUP_CONFIRMED/)).toBeInTheDocument());
    expect(screen.getByText("기준 시각 · 2분 전")).toBeInTheDocument();
    expect(screen.queryByTestId("data-freshness-stale-badge")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
  });

  it("다음/이전 커서로 이동한다", async () => {
    const fetchPage = vi.fn(async (cursor: string | undefined) => {
      if (cursor === undefined) {
        return { entries: [entry({ entry_id: "e-1" })], meta: meta({ page: { total: null, page: null, size: 20, next_cursor: "cur-2" } }) };
      }
      expect(cursor).toBe("cur-2");
      return { entries: [entry({ entry_id: "e-2", sequence_no: 99, event_ref: "topup:99" })], meta: meta() };
    });
    renderPage(fetchPage);

    await waitFor(() => expect(screen.getByText(/topup:45/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(screen.getByText(/topup:99/)).toBeInTheDocument());
    expect(screen.queryByText(/topup:45/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "이전" }));
    await waitFor(() => expect(screen.getByText(/topup:45/)).toBeInTheDocument());
  });

  it("차대 불일치 항목이 있어도 조용히 숨기지 않는다", async () => {
    const unbalanced = entry({ lines: [DEBIT_LINE, { ...CREDIT_LINE, amount: "999.00" }] });
    renderPage(async () => ({ entries: [unbalanced], meta: meta() }));

    await waitFor(() => expect(screen.getByTestId("sum-mismatch-badge")).toBeInTheDocument());
  });

  it("파싱 실패 항목이 있어도 조용히 숨기지 않는다", async () => {
    const { entry_hash: _drop, ...malformed } = entry();
    renderPage(async () => ({ entries: [malformed], meta: meta() }));

    await waitFor(() => expect(screen.getByText("거래내역을 해석할 수 없습니다.")).toBeInTheDocument());
  });

  it("as_of가 staleAfterSec을 넘기면 경고 배지를 보여준다", async () => {
    const now = new Date("2026-09-03T00:10:00Z");
    renderPage(async () => ({ entries: [entry()], meta: meta({ as_of: "2026-09-03T00:00:00Z" }) }), now);

    await waitFor(() => expect(screen.getByTestId("data-freshness-stale-badge")).toBeInTheDocument());
  });

  it("fetchPage가 ApiError 봉투로 실패하면 매핑된 메시지와 지원코드를 보여준다", async () => {
    renderPage(async () => {
      throw new ApiError(503, "server message", "trace-ledger-1", "DEPENDENCY_NOT_READY");
    });

    await waitFor(() =>
      expect(screen.getByText("서비스가 준비 중입니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(screen.getByText("지원코드: trace-ledger-1")).toBeInTheDocument();
  });

  it("fetchPage가 없으면 기본 구현이 표시 불가 오류를 routeApiError+ErrorMessage 경로로 보여준다", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <LedgerHistoryPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText("원장 내역 조회 API가 아직 제공되지 않습니다.")).toBeInTheDocument());
  });
});
