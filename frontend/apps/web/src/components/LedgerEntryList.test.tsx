import "@testing-library/jest-dom/vitest";
import type { ApiResponsePageMeta } from "@aios/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LedgerEntryList } from "./LedgerEntryList";

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

describe("LedgerEntryList", () => {
  it("로딩 중에는 불러오는 중 상태를 보여준다", () => {
    render(<LedgerEntryList entries={[]} isLoading />);
    expect(screen.getByText("불러오는 중...")).toBeInTheDocument();
  });

  it("항목이 없으면 빈 상태를 보여준다", () => {
    render(<LedgerEntryList entries={[]} />);
    expect(screen.getByText("거래내역이 없습니다.")).toBeInTheDocument();
  });

  it("정상 항목은 차변/대변 라인을 표로 보여준다", () => {
    render(<LedgerEntryList entries={[entry()]} />);
    expect(screen.getByText(/TOPUP_CONFIRMED/)).toBeInTheDocument();
    expect(screen.getByText("차변")).toBeInTheDocument();
    expect(screen.getByText("대변")).toBeInTheDocument();
    expect(screen.getAllByText("1000.00")).toHaveLength(2);
    expect(screen.queryByTestId("sum-mismatch-badge")).not.toBeInTheDocument();
  });

  it("Σ차변≠Σ대변이면 합계 불일치를 조용히 감추지 않고 표시한다", () => {
    const unbalanced = entry({ lines: [DEBIT_LINE, { ...CREDIT_LINE, amount: "999.00" }] });
    render(<LedgerEntryList entries={[unbalanced]} />);
    expect(screen.getByTestId("sum-mismatch-badge")).toBeInTheDocument();
    expect(screen.getByText(/차변 합계와 대변 합계가 일치하지 않습니다/)).toBeInTheDocument();
  });

  it("재생된 항목은 재생됨 배지를 보여준다", () => {
    render(<LedgerEntryList entries={[entry({ replayed: true })]} />);
    expect(screen.getByText("재생됨")).toBeInTheDocument();
  });

  it("필드 누락 등으로 판독 불가한 항목은 예외 없이 오류 상태를 보여준다", () => {
    const { entry_hash: _drop, ...malformed } = entry();
    render(<LedgerEntryList entries={[malformed]} />);
    expect(screen.getByText("거래내역을 해석할 수 없습니다.")).toBeInTheDocument();
  });

  it("schema_version이 v1이 아니면 사유와 함께 오류를 표시한다", () => {
    render(<LedgerEntryList entries={[entry({ schema_version: "v2" })]} />);
    expect(screen.getByText(/지원하지 않는 schema_version입니다 \(v2\)/)).toBeInTheDocument();
  });

  it("정상 항목과 판독 불가 항목이 섞여 있어도 정상 항목만 조용히 숨기지 않는다", () => {
    const { entry_hash: _drop, ...malformed } = entry({ entry_id: "e-2", sequence_no: 43 });
    render(<LedgerEntryList entries={[entry(), malformed]} />);
    expect(screen.getAllByTestId("journal-entry-card")).toHaveLength(1);
    expect(screen.getAllByTestId("journal-entry-error")).toHaveLength(1);
  });

  it("onPageChange가 있으면 Pagination을 렌더링하고 클릭 시 콜백을 호출한다", () => {
    const onPageChange = vi.fn();
    const pageMeta: ApiResponsePageMeta = { total: 40, page: 1, size: 20, next_cursor: null };
    render(<LedgerEntryList entries={[entry()]} pageMeta={pageMeta} onPageChange={onPageChange} />);
    screen.getByText("다음").click();
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("onPageChange가 없으면 Pagination을 렌더링하지 않는다", () => {
    render(<LedgerEntryList entries={[entry()]} />);
    expect(screen.queryByText("다음")).not.toBeInTheDocument();
  });
});
