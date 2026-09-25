import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { PolicyDecisionView } from "@aios/shared-types";
import { ComplianceDecisionPanel } from "./ComplianceDecisionPanel";

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

describe("ComplianceDecisionPanel — task-6276 policy:evaluate 판정 조회", () => {
  it("정상 렌더: 기본 상태에서 커맨드 유형 입력과 평가 버튼을 보여준다", () => {
    render(<ComplianceDecisionPanel />);

    expect(screen.getByText("판정 조회(policy:evaluate)")).toBeInTheDocument();
    expect(screen.getByDisplayValue("ORDER_SUBMIT")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "평가" })).toBeInTheDocument();
  });

  it("빈 데이터: 위반 규칙이 없는 성공 판정은 '위반 규칙 없음'을 보여준다(조용한 성공 아님)", () => {
    render(<ComplianceDecisionPanel />);
    clickEvaluate();

    const onSuccess = mutate.mock.calls[0]![1].onSuccess as (d: PolicyDecisionView) => void;
    act(() => onSuccess(decision({ outcome: "ALLOW", reasonCodes: [], obligations: [], expiresAt: null })));

    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.getByText("위반 규칙 없음.")).toBeInTheDocument();
    expect(screen.getByText("만료 없음")).toBeInTheDocument();
    expect(screen.getByText("없음")).toBeInTheDocument();
  });

  it("데이터 있음: 위반 규칙·의무·만료 시각이 있으면 각각 렌더한다", () => {
    render(<ComplianceDecisionPanel />);
    clickEvaluate();

    const onSuccess = mutate.mock.calls[0]![1].onSuccess as (d: PolicyDecisionView) => void;
    act(() =>
      onSuccess(
        decision({
          outcome: "DENY",
          reasonCodes: ["POLICY_LIVE_BLOCKED"],
          obligations: ["REQUIRE_APPROVAL"],
          expiresAt: "2026-09-10T00:00:00Z",
        }),
      ),
    );

    expect(screen.getByText("DENY")).toBeInTheDocument();
    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.getByText("REQUIRE_APPROVAL")).toBeInTheDocument();
    expect(screen.getByText("2026-09-10T00:00:00Z")).toBeInTheDocument();
    expect(screen.queryByText("위반 규칙 없음.")).not.toBeInTheDocument();
  });

  it("에러 상태: 평가 자체가 실패하면 판정 대신 taxonomy 에러 표면을 보여준다", () => {
    render(<ComplianceDecisionPanel />);
    clickEvaluate();

    const onError = mutate.mock.calls[0]![1].onError as (e: unknown) => void;
    act(() => onError(new ApiError(500, "raw evaluate failure", "trace-1", "INTERNAL_ERROR")));

    expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
    expect(screen.queryByText("raw evaluate failure")).not.toBeInTheDocument();
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
  });

  it("권한 없음: 403 응답은 결과 패널 대신 ForbiddenNotice 경로로만 렌더한다", () => {
    render(<ComplianceDecisionPanel />);
    clickEvaluate();

    const onError = mutate.mock.calls[0]![1].onError as (e: unknown) => void;
    act(() => onError(new ApiError(403, "forbidden raw", "trace-2", "POLICY_LIVE_BLOCKED")));

    expect(screen.queryByText("forbidden raw")).not.toBeInTheDocument();
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
  });

  it("재시도: 에러 표면에서 재시도를 누르면 evaluate를 다시 호출한다", () => {
    render(<ComplianceDecisionPanel />);
    clickEvaluate();

    const onError = mutate.mock.calls[0]![1].onError as (e: unknown) => void;
    act(() => onError(new ApiError(409, "conflict", "trace-3", "STATE_CONCURRENCY_CONFLICT")));

    mutate.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
