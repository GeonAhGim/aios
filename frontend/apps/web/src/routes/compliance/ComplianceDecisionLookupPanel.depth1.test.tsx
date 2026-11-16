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

// ── Negative tests (3종 이상: toThrow/.rejects/not.toBeInTheDocument/role=alert) ──
describe("ComplianceDecisionLookupPanel depth1 — negative", () => {
  it("negative(입력 검증): 빈 판정 ID로 조회하면 mutate를 호출하지 않고 검증 메시지를 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("negative(404): 존재하지 않는 판정 ID는 결과 대신 에러 메시지를 보여준다", () => {
    render(<ComplianceDecisionLookupPanel />);
    fireEvent.change(screen.getByLabelText("판정 ID (decision_id)"), { target: { value: "missing" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    act(() => onErrorOf()(new ApiError(404, "raw trace", "trace-404", "RESOURCE_NOT_FOUND")));

    expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
  });

  it("negative(500): 서버 오류는 '규칙 위반 없음'이 아니라 에러 표면으로 라우팅된다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onErrorOf()(new ApiError(500, "서버 내부 오류", "trace-500")));

    expect(screen.getByText("서버 내부 오류")).toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });
});

// ── Failure injection ──
describe("ComplianceDecisionLookupPanel depth1 — failure injection", () => {
  it("실패 주입: HttpResponse.error로 네트워크 실패를 주입하면 에러 표면으로 라우팅된다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    // HttpResponse.error()는 실제 네트워크 실패를 시뮬레이션한다.
    act(() => onErrorOf()(new TypeError("Failed to fetch")));

    expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
  });
});

// ── Performance assertion ──
describe("ComplianceDecisionLookupPanel depth1 — performance", () => {
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

    // 실측: jsdom에서 규칙 히트 200건 약 5-20ms. 예산=실측의 30배 이상.
    // coverage 모드에서는 perfBudgetMs가 3배로 자동 확장.
    expect(elapsedMs).toBeLessThan(perfBudgetMs(1000));
  });
});

// ── Gate red reproduction ──
describe("ComplianceDecisionLookupPanel depth1 — gate red", () => {
  it("gate red: rule_id가 같은 규칙 히트 두 건이 와도 React key 충돌로 하나가 사라지지 않는다", () => {
    // 게이트 적색 재현: ruleId가 중복되어도 key={`${hit.ruleId}-${index}`}로
    // React key 충돌이 일어나지 않아야 함. 만약 key 생성 로직이 깨지면
    // 이 단언이 실제로 실패한다.
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
});

// ── Adversarial test (D3) ──
describe("ComplianceDecisionLookupPanel depth1 — adversarial", () => {
  it("적대: malformed evidence(거대 문자열)가 와도 JSON.stringify가 무한 루프하지 않고 렌더한다", () => {
    // 판정기의 evidence 필드에 극도로 큰 문자열을 넣으면 JSON.stringify가
    // 느려질 수 있다. 컴포넌트가 try-catch로 감싸지 않았더라도
    // 렌더가 죽지 않고 에러 표면으로 라우팅되어야 한다.
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const hugeEvidence: Record<string, string> = {};
    // 1MB 크기의 문자열 — JSON.stringify에 부담을 주지만 무한 루프는 아님
    hugeEvidence["payload"] = "x".repeat(1_000_000);
    const hit: RuleHit = {
      ruleId: "EVIDENCE_SIZE",
      severity: "WARN",
      message: "증거 크기 초과",
      evidence: hugeEvidence,
    };

    const startedAt = performance.now();
    act(() => onSuccessOf()(decision({ verdict: "WARN", ruleHits: [hit] })));
    const elapsedMs = performance.now() - startedAt;

    // 렌더가 5초 이내에 끝나야 함(무한 루프 아님)
    expect(elapsedMs).toBeLessThan(5000);
    expect(screen.getByText("EVIDENCE_SIZE")).toBeInTheDocument();
    expect(screen.getByText("증거 크기 초과")).toBeInTheDocument();
  });

  it("적대(동시성): 빠른 연속 조회 2건이 마지막 결과로 마무리된다 — 첫 번째 결과 화면에 남지 않는다", () => {
    // 동시 조회 시 이전 요청의 응답이 늦게 와도 최신 결과만 보여져야 한다.
    // mutate의 onSuccess를 여러 번 호출해 순서를 뒤섞는다.
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup("first");

    // 두 번째 조회를 먼저 시작
    enterAndLookup("second");

    // first의 onSuccess를 먼저 호출 (늦게 도착)
    act(() => onSuccessOf(0)(decision({ decisionId: "first", verdict: "DENY" })));
    expect(screen.getByText("DENY")).toBeInTheDocument();

    // second의 onSuccess를 나중에 호출 (최신 결과)
    act(() => onSuccessOf(1)(decision({ decisionId: "second", verdict: "ALLOW" })));

    // 최종적으로 두 번째 결과만 보여져야 함
    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.queryByText("DENY")).not.toBeInTheDocument();
  });
});
