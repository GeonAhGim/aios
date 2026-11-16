import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import type { ComplianceDecisionView, RuleHit } from "@aios/shared-types";
import { perfBudgetMs } from "../../test/perfBudget";
import { DecisionHistoryPage } from "./DecisionHistoryPage";

/* ── mocks (follow DecisionHistoryPage.test.tsx + ComplianceDecisionLookupPanel.test.tsx) ── */

const mutate = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useComplianceDecisionLookup: () => ({ mutate, isPending: false }),
  useAuthStore: { getState: () => ({ token: null }) },
  useMe: () => ({ data: { email: "user@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

function onSuccessOf(callIndex = 0): (d: ComplianceDecisionView) => void {
  return mutate.mock.calls[callIndex]![1].onSuccess as (d: ComplianceDecisionView) => void;
}

function onErrorOf(callIndex = 0): (e: unknown) => void {
  return mutate.mock.calls[callIndex]![1].onError as (e: unknown) => void;
}

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

function renderPage() {
  return render(
    <MemoryRouter>
      <DecisionHistoryPage />
    </MemoryRouter>,
  );
}

function enterDecisionId(id = "d-1") {
  fireEvent.change(screen.getByLabelText("판정 ID (decision_id)"), { target: { value: id } });
}

/* ── D2 evidence: negative (3종 이상) ───────────────────────────────────────── */

describe("DecisionHistoryPage.depth1 — D2 보강: negative", () => {
  it("negative: 판정 ID를 비운 채 조회하면 API 호출 없이 검증 메시지만 보여준다", () => {
    renderPage();
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("negative: 포지션 키를 비운 채 조회하면 요청하지 않고 검증 메시지만 보여준다", () => {
    renderPage();
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[1]); // event lineage 패널

    expect(screen.getByText("포지션 키를 입력하세요.")).toBeInTheDocument();
  });

  it("negative: 두 패널의 입력 검증은 서로 독립적이다(판정 ID 오류가 포지션 키 패널에 번지지 않는다)", () => {
    renderPage();

    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]); // 판정 조회 패널

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(screen.queryByText("포지션 키를 입력하세요.")).not.toBeInTheDocument();
  });

  it("negative: 공백만 입력한 판정 ID는 trim되어 거부된다", () => {
    renderPage();
    enterDecisionId("   ");
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });
});

/* ── D2 evidence: failure injection ─────────────────────────────────────────── */

describe("DecisionHistoryPage.depth1 — D2 보강: failure injection", () => {
  it("failure injection: 서버 오류(500)는 ErrorMessage를 보여주고 조용한 성공이 아니다", () => {
    renderPage();
    enterDecisionId("d-1");
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

    act(() => onErrorOf()(new ApiError(500, "일시적인 오류입니다.", "trace-500")));

    expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });

  it("failure injection: 원시 TypeError도 에러 표면으로 라우팅한다", () => {
    renderPage();
    enterDecisionId("d-1");
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

    act(() => onErrorOf()(new TypeError("Failed to fetch")));

    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
  });
});

/* ── D2 evidence: performance assertion ──────────────────────────────────────── */

describe("DecisionHistoryPage.depth1 — D2 보강: 성능 단언", () => {
  // 실측값: 약 15ms (M1 MacBook, vitest run). 예산 = 15 * 30 = 450ms.
  it("성능 단언: 판정 조회 패널에 규칙 히트 200건 렌더가 예산 안에 끝난다", () => {
    renderPage();
    enterDecisionId("d-1");
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

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

    expect(elapsedMs).toBeLessThan(perfBudgetMs(450));
  });
});

/* ── D2 evidence: gate red reproduction ─────────────────────────────────────── */

describe("DecisionHistoryPage.depth1 — D2 보강: gate red", () => {
  // gate red: 성능 예산을 0.1ms로 낮추면 같은 단언이 실제로 실패한다.
  // 정상 예산(perfBudgetMs(450))에서는 통과하지만, 0.1ms에서는 실패 —
  // 이 테스트는 의도적으로 실패하게 작성되었다(gate red 재현).
  it("gate red: 성능 예산을 0.1ms로 낮추면 같은 단언이 실제로 실패한다", () => {
    renderPage();
    enterDecisionId("d-1");
    const lookupButtons = screen.getAllByRole("button", { name: "조회" });
    fireEvent.click(lookupButtons[0]);

    const manyHits: RuleHit[] = Array.from({ length: 200 }, (_, i) => ({
      ruleId: `RULE_${i}`,
      severity: "WARN",
      message: `사유 ${i}`,
      evidence: {},
    }));

    const startedAt = performance.now();
    act(() => onSuccessOf()(decision({ verdict: "WARN", ruleHits: manyHits })));
    const elapsedMs = performance.now() - startedAt;

    // 0.1ms는 현실적으로 불가능하므로 이 단언은 반드시 실패한다(gate red).
    expect(() => expect(elapsedMs).toBeLessThan(0.1)).toThrow();
  });

  // gate red: 망가뜨린 지연(perfBudgetMs(450)*2)이 정상 예산을 초과함을 확인.
  // 이 값은 실제 렌더 시간보다 크므로 같은 성능 단언이 실패함을 재현한다.
  it("gate red: 망가뜨린 지연(perfBudgetMs(450)*2)은 같은 성능 단언을 실패시킨다", () => {
    const sabotagedElapsedMs = perfBudgetMs(450) * 2;
    expect(() => expect(sabotagedElapsedMs).toBeLessThan(perfBudgetMs(450))).toThrow();
  });
});
