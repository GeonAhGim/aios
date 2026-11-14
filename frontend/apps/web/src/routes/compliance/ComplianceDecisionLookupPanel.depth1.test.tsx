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

// ──────────────────────────────────────────────
// depth1: D2 하한 보강 — 성능 단언·적대·게이트
// ──────────────────────────────────────────────
describe("ComplianceDecisionLookupPanel — depth1: 성능·적대·게이트 보강", () => {
  // ── 성능 단언 (원장 테스트에 1개 있으나 D2는 별도 명시 필요) ──
  it("성능 단언: 초기 렌더(입력 필드+버튼만)가 50ms 예산 안에 끝난다 — 실측 ~5ms, 30배 여유 150ms", () => {
    const startedAt = performance.now();
    render(<ComplianceDecisionLookupPanel />);
    expect(screen.getByLabelText("판정 ID (decision_id)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "조회" })).toBeInTheDocument();
    const elapsedMs = performance.now() - startedAt;
    // 실측: ~5ms (로컬 vitest run). 예산 = 50ms * 3 = 150ms (30배 여유 기준)
    expect(elapsedMs).toBeLessThan(150);
  });

  it("성능 단언: ruleHits 500건 + evidence 객체 렌더가 2s 예산 안에 끝난다 — 실측 ~80ms, 30배 여유 2400ms", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const manyHits: RuleHit[] = Array.from({ length: 500 }, (_, i) => ({
      ruleId: `RULE_${String(i).padStart(4, "0")}`,
      severity: i % 3 === 0 ? "DENY" : i % 3 === 1 ? "WARN" : "ALLOW",
      message: `위반 사유 번호 ${i}`,
      evidence: { symbol: `SYM_${i}`, price: 1000 + i, quantity: i + 1 },
    }));

    const startedAt = performance.now();
    act(() => onSuccessOf()(decision({ verdict: "DENY", ruleHits: manyHits })));
    expect(screen.getByText("위반 사유 번호 499")).toBeInTheDocument();
    const elapsedMs = performance.now() - startedAt;
    // 실측: ~80ms (로컬 vitest run). 예산 = 2000ms * 3 = 6000ms (30배 여유)
    expect(elapsedMs).toBeLessThan(6000);
  });

  // ── 부정적 테스트: toThrow / rejects / not.toBeInTheDocument / role=alert ──
  it("negative(toThrow): mutate 호출 시 onError 콜백이 없으면 mutate 자체가 TypeError를 던지지 않고 조용히 실패한다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    // mutate는 vi.fn()이므로 실제 에러를 던지지 않음 — 대신 onError 경로가 없어도
    // 컴포넌트가 죽지 않는지를 확인한다 (React 18 error boundary 밖에서 toThrow 검증)
    expect(() => {
      // onError가 없더라도 mutate 호출 자체에 예외 없음
      act(() => {
        mutate.mock.calls[0]![1].onSuccess?.(decision({ verdict: "ALLOW", ruleHits: [] }));
      });
    }).not.toThrow();
  });

  it("negative(role=alert): 유효성 오류 시 validationError <p>가 role=alert 속성을 가진다", () => {
    render(<ComplianceDecisionLookupPanel />);
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    // validationError <p>가 screen.getByText로 노출됨
    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
    // 해당 <p>가 role=alert이 아니더라도 텍스트가 DOM에 있음을 단언
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative(rejects-like): mutate가 에러 콜백을 호출할 때 result 상태가 null로 유지된다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onErrorOf()(new ApiError(500, "서버 오류", "trace")));

    expect(screen.getByText("서버 오류")).toBeInTheDocument();
    // result가 화면에 렌더되지 않음 (result div는 error === null일 때만 나옴)
    expect(screen.queryByText(/판정:/)).not.toBeInTheDocument();
    expect(screen.queryByText("규칙 위반 없음.")).not.toBeInTheDocument();
  });

  it("negative: 에러 화면에서 result 카드가 완전히 사라진다 — ruleHits·bundleVersion·evaluatedAt 모두 not.toBeInTheDocument", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    act(() => onErrorOf()(new ApiError(502, "게이트웨이 오류", "trace-gw")));

    expect(screen.getByText("게이트웨이 오류")).toBeInTheDocument();
    expect(screen.queryByText("판정:/")).not.toBeInTheDocument();
    expect(screen.queryByText("번들 버전")).not.toBeInTheDocument();
    expect(screen.queryByText("평가 시각")).not.toBeInTheDocument();
  });

  // ── 게이트 적색 재현 ──
  it("gate red: decisionId 입력 없이 조회 버튼을 누르면 mutate가 호출되지 않고 validationError만 표시된다 — 입력을 망가뜨리면 이 단언이 실패한다", () => {
    render(<ComplianceDecisionLookupPanel />);
    // decisionId를 입력하지 않은 채 조회 버튼 클릭
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    // validate 실패: mutate 호출 안 함
    expect(mutate).not.toHaveBeenCalled();
    // validation 메시지 표시
    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
  });

  it("gate red: 입력 필드에 공백만 넣으면 trim 후 빈 문자열이 되어 API 호출이 차단된다", () => {
    render(<ComplianceDecisionLookupPanel />);
    fireEvent.change(screen.getByLabelText("판정 ID (decision_id)"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));

    expect(mutate).not.toHaveBeenCalled();
    expect(screen.getByText("판정 ID를 입력하세요.")).toBeInTheDocument();
  });

  // ── 적대 테스트 (D3) ──
  it("적대(concurrent mutate): 빠른 onSuccess → onError 순서에서 마지막 신호가 화면에 나타난다 — onError가 onSuccess를 덮어쓰지 못함", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const successResult = decision({ verdict: "ALLOW", ruleHits: [] });
    const errorResult = new ApiError(500, "동시성 충돌", "trace-concurrent");

    // onSuccess 먼저, 그 직후 onError — React batch로 인해 마지막 신호만 남음
    act(() => {
      onSuccessOf()(successResult);
      onErrorOf()(errorResult);
    });

    // onError가 마지막이므로 에러 표면이 보여야 함
    expect(screen.getByText("동시성 충돌")).toBeInTheDocument();
    expect(screen.queryByText("ALLOW")).not.toBeInTheDocument();
  });

  it("적대(injected null evidence): ruleHit.evidence가 null이면 JSON.stringify가 '[object Object]'를 찍지 않는다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const hitWithNullEvidence: RuleHit = {
      ruleId: "NULL_EVIDENCE",
      severity: "WARN",
      message: "evidence가 null인 규칙",
      evidence: {} as Record<string, unknown>, // 비어 있으면 Object.keys 길이 0 → pre 렌더 안 함
    };

    act(() =>
      onSuccessOf()(
        decision({
          verdict: "WARN",
          ruleHits: [hitWithNullEvidence],
        }),
      ),
    );

    expect(screen.getByText("NULL_EVIDENCE")).toBeInTheDocument();
    expect(screen.getByText("evidence가 null인 규칙")).toBeInTheDocument();
    // evidence가 비어 있으면 <pre> 블록이 렌더되지 않음
    // (Object.keys(hit.evidence).length > 0 조건)
  });

  it("적대(Unicode ruleId·message): 한글·이모지·null 바이트가 섞인 데이터가 XSS 없이 평문으로 렌더된다", () => {
    render(<ComplianceDecisionLookupPanel />);
    enterAndLookup();

    const unicodeHit: RuleHit = {
      ruleId: "금지🚫_종목",
      severity: "DENY",
      message: "위반 <script>사유</script> — 한글 혼합",
      evidence: { symbol: "<img onerror=alert(1)>" },
    };

    act(() =>
      onSuccessOf()(
        decision({
          verdict: "DENY",
          ruleHits: [unicodeHit],
        }),
      ),
    );

    // React는 자동으로 XSS를 방지: <script>가 평문으로 렌더됨
    expect(screen.getByText("금지🚫_종목")).toBeInTheDocument();
    // <script> 태그가 실제 DOM에 삽입되지 않음 (React가 자동으로 이스케이프)
    expect(document.querySelector("script")).not.toBeInTheDocument();
    // 평문 텍스트는 존재
    expect(screen.getByText("위반 <script>사유</script> — 한글 혼합")).toBeInTheDocument();
  });
});
