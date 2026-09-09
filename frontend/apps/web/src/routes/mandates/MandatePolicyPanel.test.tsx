import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { PolicyDecisionView } from "@aios/shared-types";
import { MandatePolicyPanel } from "./MandatePolicyPanel";

const mutate = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useEvaluateMandatePolicy: () => ({ mutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

function decision(overrides: Partial<PolicyDecisionView>): PolicyDecisionView {
  return {
    id: "d-1",
    tenantId: "t-1",
    bundleId: "b-1",
    commandType: "ORDER_SUBMIT",
    outcome: "DENY",
    reasonCodes: [],
    obligations: [],
    evaluatedAt: "2026-09-09T00:00:00Z",
    expiresAt: null,
    schemaVersion: "v1",
    ...overrides,
  };
}

function clickEvaluate() {
  fireEvent.click(screen.getByRole("button", { name: "평가" }));
}

describe("MandatePolicyPanel — task-2336 policy:evaluate 표시", () => {
  it("정상 렌더: 위반 규칙이 있으면 판정과 사유 목록을 보여준다", () => {
    render(<MandatePolicyPanel />);
    clickEvaluate();

    const onSuccess = mutate.mock.calls[0]![1].onSuccess as (d: PolicyDecisionView) => void;
    act(() => onSuccess(decision({ outcome: "DENY", reasonCodes: ["POLICY_LIVE_BLOCKED"] })));

    expect(screen.getByText("DENY")).toBeInTheDocument();
    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.queryByText("위반 규칙 없음.")).not.toBeInTheDocument();
  });

  it("빈 상태: 위반 규칙이 없는 성공 판정은 '위반 규칙 없음'을 보여준다(조용한 성공 아님)", () => {
    render(<MandatePolicyPanel />);
    clickEvaluate();

    const onSuccess = mutate.mock.calls[0]![1].onSuccess as (d: PolicyDecisionView) => void;
    act(() => onSuccess(decision({ outcome: "ALLOW", reasonCodes: [] })));

    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.getByText("위반 규칙 없음.")).toBeInTheDocument();
  });

  it("에러·거부 입력: 평가 자체가 실패하면 판정 대신 taxonomy 에러 표면을 보여준다", () => {
    render(<MandatePolicyPanel />);
    clickEvaluate();

    const onError = mutate.mock.calls[0]![1].onError as (e: unknown) => void;
    act(() => onError(new ApiError(500, "raw evaluate failure", "trace-1", "INTERNAL_ERROR")));

    expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
    expect(screen.queryByText("raw evaluate failure")).not.toBeInTheDocument();
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
  });
});
