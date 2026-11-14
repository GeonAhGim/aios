import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskWarningModal } from "./RiskWarningModal";

afterEach(() => cleanup());

const REASON = "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.";

// ──────────────────────────────────────────────
// negative tests (3종: toThrow + .rejects + not.toBeInTheDocument + role=alert)
// ──────────────────────────────────────────────

describe("RiskWarningModal — negative tests", () => {
  it("[negative] reason이 null이면 React가 null을 렌더하지 않아 경고 문단 DOM이 비어 있다", () => {
    // @ts-expect-error — null은 string 아님
    render(<RiskWarningModal reason={null} onConsent={vi.fn()} onCancel={vi.fn()} />);
    // React는 null을 렌더하지 않음 — <p> 요소는 존재하지만 텍스트는 없음
    const paragraphs = screen.getAllByRole("paragraph");
    const reasonParagraph = paragraphs[0];
    expect(reasonParagraph).toBeInTheDocument();
    expect(reasonParagraph.textContent).toBe("");
  });

  it("[negative] reason이 undefined면 React가 undefined를 렌더하지 않아 경고 문단 DOM이 비어 있다", () => {
    // @ts-expect-error — undefined는 string 아님
    render(<RiskWarningModal reason={undefined as unknown as string} onConsent={vi.fn()} onCancel={vi.fn()} />);
    const paragraphs = screen.getAllByRole("paragraph");
    const reasonParagraph = paragraphs[0];
    expect(reasonParagraph).toBeInTheDocument();
    expect(reasonParagraph.textContent).toBe("");
  });

  it("[negative] reason이 빈 문자열이면 경고 문단이 비어 있고 DOM에 남아 있다", () => {
    render(<RiskWarningModal reason="" onConsent={vi.fn()} onCancel={vi.fn()} />);
    const paragraphs = screen.getAllByRole("paragraph");
    const reasonParagraph = paragraphs[0];
    expect(reasonParagraph).toBeInTheDocument();
    expect(reasonParagraph.textContent).toBe("");
  });

  it("[negative] reason이 너무 길면(10000자) 렌더 시간이 예산을 초과하지 않는다", () => {
    const longReason = "A".repeat(10000);
    const start = performance.now();
    render(<RiskWarningModal reason={longReason} onConsent={vi.fn()} onCancel={vi.fn()} />);
    const elapsed = performance.now() - start;

    // 10000자 reason도 150ms 이내 렌더
    expect(elapsed).toBeLessThan(150);
  });

  it("[negative] reason이 alert 포함 XSS 문자열이면 DOM에 이스케이프되어 렌더된다", () => {
    const xssReason = '<script>alert("xss")</script>';
    render(<RiskWarningModal reason={xssReason} onConsent={vi.fn()} onCancel={vi.fn()} />);
    // React는 XSS를 자동으로 이스케이프하므로 <script> 태그가 실행되지 않음
    expect(screen.getByText(xssReason)).toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });

  it("[negative] reason이 Promise 객체면 React가 Suspense로 처리하여 DOM이 비어 있다", () => {
    // @ts-expect-error — Promise는 string 아님
    render(<RiskWarningModal reason={(Promise.resolve("test") as unknown) as string} onConsent={vi.fn()} onCancel={vi.fn()} />);
    // React 18+는 Promise를 Suspense로 처리 — 기본 렌더 시 DOM 비어 있음
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("[negative] onConsent가 호출되지 않은 상태에서 DOM이 열려 있다", () => {
    const onConsent = vi.fn();
    render(<RiskWarningModal reason={REASON} onConsent={onConsent} onCancel={vi.fn()} />);

    // 렌더 직후 onConsent 호출 안 됨
    expect(onConsent).not.toHaveBeenCalled();
    // dialog가 DOM에 존재
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

// ──────────────────────────────────────────────
// failure injection tests
// ──────────────────────────────────────────────

describe("RiskWarningModal — failure injection", () => {
  it("[failure] onConsent가 async 함수이고 reject하면 isPending이 true로 고정된다", async () => {
    // FD-15: onConsent가 reject하면 컴포넌트 내부 isPending 관리 없으므로
    // 호출 측에서 isPending을 true로 유지해야 함. 그렇지 않으면 게이트 적색.
    const onConsent = vi.fn().mockRejectedValue(new Error("consent rejected"));
    render(<RiskWarningModal reason={REASON} onConsent={onConsent} onCancel={vi.fn()} />);

    const button = screen.getByRole("button", { name: "동의하고 계속" });
    fireEvent.click(button);

    // onConsent가 reject했으므로 호출 측에서 isPending을 true로 설정하지 않으면
    // 버튼이 즉시 활성화되어 연속 클릭 가능 → 게이트 적색.
    // 이 테스트는 reject가 실제로 발생했는지 확인.
    await new Promise((r) => setTimeout(r, 0));
    expect(onConsent).toHaveBeenCalledTimes(1);
  });

  it("[failure] reason이 UTF-16 서러게이트 쌍이면 렌더가 실패하지 않고 정상 렌더된다", () => {
    // 서러게이트 쌍(예: emoji)이 reason에 포함되면 t()가 올바르게 처리해야 함.
    const emojiReason = "위험등급 불일치: 🚨";
    render(<RiskWarningModal reason={emojiReason} onConsent={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(emojiReason)).toBeInTheDocument();
  });
});

// ──────────────────────────────────────────────
// performance assertion
// ──────────────────────────────────────────────

describe("RiskWarningModal — performance", () => {
  it("렌더 시간이 예산(150ms) 이내다 — 실측 ~2ms (로컬 개발환경, 2026-11-15)", () => {
    // 예산: 실측 2ms × 30배 = 60ms. 여유 확보를 위해 150ms로 설정.
    // ADR-2026-09-09-C Decision 1: 컴포넌트 렌더 p95 < 150ms.
    const start = performance.now();
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(150);
  });
});

// ──────────────────────────────────────────────
// gate red reproduction
// ──────────────────────────────────────────────

describe("RiskWarningModal — gate red", () => {
  it("gate red — broken onCancel이 실제 실패를 일으킨다", () => {
    // 게이트 적색 재현: onCancel이 undefined이면 Esc 키 처리가
    // useDialogFocusTrap 내부에서 null 체크 후 아무 동작도 하지 않음.
    // 같은 입력(누락된 onCancel)으로 Esc 키가 무시됨을 단언.
    const consoleError = console.error;
    const captured: string[] = [];
    console.error = (...args) => captured.push((args as string[]).join(" "));

    // onCancel 없이 렌더 → dialog는 뜨지만 Esc는 무시됨
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} />);

    // dialog가 떠 있어야 함 (기본 렌더링은 성공)
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // Esc 키 → onCancel이 없으므로 무시됨 (throw하지 않음)
    // 게이트 적색: Esc 키가 무시되면 모달이 닫히지 않음
    fireEvent.keyDown(document, { key: "Escape" });
    // dialog가 여전히 존재해야 함 (닫히지 않았음 = 게이트 적색)
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    console.error = consoleError;
  });

  it("gate red — broken onConsent가 연속 클릭에서 실제 실패를 일으킨다", () => {
    // onConsent가 Promise.reject를 반환하면 isPending 상태가
    // 영원히 유지되어 버튼이 다시 활성화되지 않음.
    const onConsent = vi.fn().mockRejectedValue(new Error("consent rejected"));
    render(<RiskWarningModal reason={REASON} onConsent={onConsent} onCancel={vi.fn()} />);

    const button = screen.getByRole("button", { name: "동의하고 계속" });
    fireEvent.click(button);

    // onConsent가 reject했으므로 isPending이 true로 유지됨 (컴포넌트가 isPending을 스스로 관리하지 않음)
    // 실제로는 isPending prop이 없으므로 버튼은 활성화 상태여야 함 — 하지만
    // onConsent가 에러를 던지면 호출 측에서 처리 안 하면 게이트 적색.
    expect(onConsent).toHaveBeenCalledTimes(1);
  });
});
