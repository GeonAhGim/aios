import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { ComplianceDecisionView, RuleHit } from "@aios/shared-types";
import { perfBudgetMs } from "../../test/perfBudget";
import { ComplianceDecisionLookupPanel } from "./ComplianceDecisionLookupPanel";

const mutate = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useComplianceDecisionLookup: () => ({ mutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

function decision(overrides: Partial<ComplianceDecisionView>): ComplianceDecisionView {
  return {
    decisionId: "d-1",
    verdict: "DENY",
    ruleHits: [],
    inputsHash: "a".repeat(64),
    bundleVersion: "b".repeat(64),
    evaluatedAt: "2026-09-09T00:00:00Z",
    schemaVersion: "v1",
    ...overrides,
  };
}

function enterAndLookup(decisionId = "d-1") {
  fireEvent.change(screen.getByLabelText("판정 ID (decision_id)"), { target: { value: decisionId } });
  fireEvent.click(screen.getByRole("button", { name: "조회" }));
}

function onSuccessOf(callIndex = 0): (d: ComplianceDecisionView) => void {
  return mutate.mock.calls[callIndex]![1].onSuccess as (d: ComplianceDecisionView) => void;
}

function onErrorOf(callIndex = 0): (e: unknown) => void {
  return mutate.mock.calls[callIndex]![1].onError as (e: unknown) => void;
}

describe("ComplianceDecisionLookupPanel — task-2668 CM-18 판정 조회·규칙 히트", () => {
  it("positive: 위반이 있으면 판정과 규칙 히트(rule_id·severity·message)를 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const hit: RuleHit = {
      ruleId: "RESTRICTED_LIST",
      severity: "DENY",
      message: "금지 종목입니다.",
      evidence: { symbol: "XYZ" },
    };
    act(() => onSuccessOf()(decision({ verdict: "DENY", ruleHits: [hit] })));

    expect(screen.getAllByText("DENY")).toHaveLength(2); // 판정 배지 + 규칙 심각도 배지
    expect(screen.getByText("RESTRICTED_LIST")).toBeInTheDocument();
    expect(screen.getByText("금지 종목입니다.")).toBeInTheDocument();
    expect(screen.getByText('{"symbol":"XYZ"}')).toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });

  it("positive: 위반이 없으면(ruleHits=[]) '규칙 위반 없음'을 보여준다(조용한 성공 아님)", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onSuccessOf()(decision({ verdict: "ALLOW", ruleHits: [] })));

    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.getByText("규칙 위반 없음.")).toBeInTheDocument();
  });

  it("negative(입력 검증): 판정 ID를 비운 채 조회하면 API를 호출하지 않고 검증 메시지만 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("negative: 존재하지 않는 판정 ID(404)는 결과 대신 에러 표면을 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup("missing-id");

    act(() => onErrorOf()(new ApiError(404, "raw not-found message", "trace-404", "RESOURCE_NOT_FOUND")));

    expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw not-found message")).not.toBeInTheDocument();
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });

  it("negative: 서버 오류(500)는 '규칙 위반 없음'이 아니라 ErrorMessage를 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onErrorOf()(new ApiError(500, "일시적인 오류입니다.", "trace-500")));

    expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });

  it("실패 주입: ApiError가 아닌 원시 네트워크 예외(TypeError)도 죽지 않고 에러 표면으로 라우팅한다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onErrorOf()(new TypeError("Failed to fetch")));

    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
  });

  it("게이트 적색 재현: rule_id가 같은 규칙 히트 두 건이 와도 React key 충돌로 하나가 사라지지 않는다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const duplicateHits: RuleHit[] = [
      { ruleId: "CONCENTRATION", severity: "WARN", message: "첫 번째 경고", evidence: {} },
      { ruleId: "CONCENTRATION", severity: "WARN", message: "두 번째 경고", evidence: {} },
    ];
    act(() => onSuccessOf()(decision({ verdict: "WARN", ruleHits: duplicateHits })));

    expect(screen.getByText("첫 번째 경고")).toBeInTheDocument();
    expect(screen.getByText("두 번째 경고")).toBeInTheDocument();
    expect(screen.getAllByText("CONCENTRATION")).toHaveLength(2);
  });

  it("성능 단언: 규칙 히트 200건 렌더가 예산 안에 끝난다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const manyHits: RuleHit[] = Array.from({ length: 200 }, (_, i) => ({
      ruleId: `RULE_${i}`,
      severity: "WARN",
      message: `사유 ${i}`,
      evidence: {},
    }));

    const startedAt = performance.now();
    act(() => onSuccessOf()(decision({ verdict: "WARN", ruleHits: manyHits })));
    expect(screen.getByText("사유 199")).toBeInTheDocument();
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(1000));
  });
});
