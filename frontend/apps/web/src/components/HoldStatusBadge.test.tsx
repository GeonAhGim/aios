import "@testing-library/jest-dom/vitest";
import type { HoldView, ParsedHoldView, ParsedPayoutBatchView, PayoutBatchView } from "@aios/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { HoldStatusBadge, PayoutBatchStatusBadge } from "./HoldStatusBadge";

// vitest.config에 globals:true가 없어 testing-library의 자동 cleanup 등록이
// 동작하지 않는다(InstrumentLifecycleBadge.test.tsx와 동일 사유).
afterEach(cleanup);

const NOW = "2026-09-03T00:00:00Z";

const HOLD: HoldView = {
  hold_id: "h-1",
  account_code: "USER:u-1:HELD",
  amount: "1000.00",
  purpose: "purchase",
  reference: "purchase:123",
  state: "PENDING",
  expires_at: "2026-09-10T00:00:00Z",
  entry_id: "e-1",
};

const PAYOUT_BATCH: PayoutBatchView = {
  batch_id: "b-1",
  seller_user_id: "u-2",
  period_start: "2026-08-01",
  period_end: "2026-08-31",
  amount: "5000.00",
  state: "SCHEDULED",
  capture_entry_ids: ["e-2", "e-3"],
  release_entry_id: null,
  paid_entry_id: null,
};

function okHold(overrides: Partial<HoldView> = {}): ParsedHoldView {
  return { kind: "ok", value: { ...HOLD, ...overrides } };
}

function okPayoutBatch(overrides: Partial<PayoutBatchView> = {}): ParsedPayoutBatchView {
  return { kind: "ok", value: { ...PAYOUT_BATCH, ...overrides } };
}

describe("HoldStatusBadge", () => {
  it("PENDING이고 미만료면 홀드 중 배지만 보여준다(확인 필요 없음)", () => {
    render(<HoldStatusBadge hold={okHold()} now={NOW} />);

    expect(screen.getByTestId("hold-state-badge")).toHaveTextContent("홀드 중");
    expect(screen.queryByTestId("hold-expiry-review-badge")).not.toBeInTheDocument();
  });

  it("CAPTURED는 확정 배지를 보여주고 확인 필요를 띄우지 않는다", () => {
    render(<HoldStatusBadge hold={okHold({ state: "CAPTURED" })} now={NOW} />);

    expect(screen.getByTestId("hold-state-badge")).toHaveTextContent("확정");
    expect(screen.queryByTestId("hold-expiry-review-badge")).not.toBeInTheDocument();
  });

  it("EXPIRED는 만료 배지를 보여준다", () => {
    render(<HoldStatusBadge hold={okHold({ state: "EXPIRED" })} now={NOW} />);

    expect(screen.getByTestId("hold-state-badge")).toHaveTextContent("만료");
  });

  it("PENDING인데 now가 expires_at을 지났으면 서버 state는 그대로 두고 확인 필요를 추가로 띄운다", () => {
    render(<HoldStatusBadge hold={okHold()} now="2026-09-11T00:00:00Z" />);

    expect(screen.getByTestId("hold-state-badge")).toHaveTextContent("홀드 중");
    expect(screen.getByTestId("hold-expiry-review-badge")).toHaveTextContent("만료 확인 필요");
  });

  it("now가 주어지지 않으면(판단 근거 없음) 확인 필요를 임의로 띄우지 않는다", () => {
    render(<HoldStatusBadge hold={okHold()} />);

    expect(screen.queryByTestId("hold-expiry-review-badge")).not.toBeInTheDocument();
  });

  it("negative: hold가 unsupported_schema_version이면 사유를 노출하고 조용히 숨기지 않는다", () => {
    render(<HoldStatusBadge hold={{ kind: "unsupported_schema_version", received: "v2" }} now={NOW} />);

    expect(screen.getByTestId("hold-status-badge")).toHaveTextContent("v2");
  });

  it("negative: hold가 invalid면 해석 불가 문구를 노출한다", () => {
    render(<HoldStatusBadge hold={{ kind: "invalid" }} now={NOW} />);

    expect(screen.getByTestId("hold-status-badge")).toHaveTextContent("해석할 수 없습니다");
  });
});

describe("PayoutBatchStatusBadge", () => {
  it("SCHEDULED는 예정 배지를 보여준다", () => {
    render(<PayoutBatchStatusBadge payoutBatch={okPayoutBatch()} />);

    expect(screen.getByTestId("payout-batch-state-badge")).toHaveTextContent("예정");
  });

  it("RELEASED는 정산 대기 배지를 보여준다", () => {
    render(<PayoutBatchStatusBadge payoutBatch={okPayoutBatch({ state: "RELEASED", release_entry_id: "e-4" })} />);

    expect(screen.getByTestId("payout-batch-state-badge")).toHaveTextContent("정산 대기");
  });

  it("PAID는 지급 완료 배지를 보여준다", () => {
    render(
      <PayoutBatchStatusBadge
        payoutBatch={okPayoutBatch({ state: "PAID", release_entry_id: "e-4", paid_entry_id: "e-5" })}
      />,
    );

    expect(screen.getByTestId("payout-batch-state-badge")).toHaveTextContent("지급 완료");
  });

  it("FAILED는 실패 배지를 보여준다", () => {
    render(<PayoutBatchStatusBadge payoutBatch={okPayoutBatch({ state: "FAILED" })} />);

    expect(screen.getByTestId("payout-batch-state-badge")).toHaveTextContent("실패");
  });

  it("negative: payoutBatch가 unsupported_schema_version이면 사유를 노출한다", () => {
    render(<PayoutBatchStatusBadge payoutBatch={{ kind: "unsupported_schema_version", received: "v3" }} />);

    expect(screen.getByTestId("payout-batch-status-badge")).toHaveTextContent("v3");
  });

  it("negative: payoutBatch가 invalid면 해석 불가 문구를 노출한다", () => {
    render(<PayoutBatchStatusBadge payoutBatch={{ kind: "invalid" }} />);

    expect(screen.getByTestId("payout-batch-status-badge")).toHaveTextContent("해석할 수 없습니다");
  });
});
