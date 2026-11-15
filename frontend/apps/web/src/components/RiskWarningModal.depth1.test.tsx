import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskWarningModal } from "./RiskWarningModal";

afterEach(() => cleanup());

const REASON = "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.";

// 성능 예산: 로컬 실측 렌더 시간 ≈ 2ms (M1 Mac, React 18).
// ADR-2026-09-09-C Decision 1 — UI 렌더 p95 ≤ 60ms (작은 모달 기준).
// 여기서는 실측의 30배인 60ms를 예산으로 둔다.

describe("RiskWarningModal depth1 — 성능·게이트 적색", () => {
  it("성능: 렌더 시간이 60ms 예산 이하이다 (실측 근거: ≈2ms, 30배 여유)", () => {
    const t0 = performance.now();
    render(
      <RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />,
    );
    const elapsed = performance.now() - t0;

    expect(elapsed).toBeLessThan(60);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    cleanup();

    // 재렌더 비용도 확인
    const t1 = performance.now();
    render(
      <RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(performance.now() - t1).toBeLessThan(60);
    cleanup();
  });

  it("gate red: reason이 유효하면 dialog가 열리고 reason 텍스트가 DOM에 있다", () => {
    const onCancel = vi.fn();
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={onCancel} />);

    // 게이트: 모달이 열려 있어야 한다 — 열려 있지 않으면 게이트 적색
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // 게이트: reason 텍스트가 DOM에 있어야 한다 — 없으면 게이트 적색
    expect(screen.getByText(REASON)).toBeInTheDocument();

    // 게이트: 제목이 존재한다
    expect(screen.getByText("위험등급 불일치 경고")).toBeInTheDocument();
  });

  it("negative: aria-hidden='true' 아이콘이 accessibility tree에 노출되지 않는다", () => {
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);

    // ⚠ span은 aria-hidden=true이므로 screen.getByRole("img")로 찾을 수 없다.
    // accessibility tree에 남지 않음을 확인.
    expect(() => screen.getByRole("img")).toThrow();

    // 대신 dialog의 textContent에 reason이 들어간다.
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain(REASON);
  });

  it("failure_injection: onCancel이 예외를 던지면 Esc 키가 uncaught exception을 발생시킨다", async () => {
    const onCancel = vi.fn().mockImplementation(() => {
      throw new Error("networkError: onCancel 예외");
    });

    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={onCancel} />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // Esc 키 — onCancel이 예외를 던짐. JSDOM 이벤트는 비동기 전파이므로
    // error 이벤트 리스너로 포착한다.
    const listener = vi.fn();
    window.addEventListener("error", listener);

    fireEvent.keyDown(document, { key: "Escape" });

    window.removeEventListener("error", listener);

    // onCancel이 호출되었고, 예외가 uncaught exception으로 발생함
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalled();
  });

  it("negative: reason이 비어있으면 dialog는 렌더되고 paragraph가 존재한다", () => {
    render(<RiskWarningModal reason="" onConsent={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const paragraphs = screen.getAllByRole("paragraph");
    expect(paragraphs.length).toBeGreaterThan(0);
  });

  it("negative: isPending=true이면 버튼이 disabled되고 spinner가 렌더된다", () => {
    render(
      <RiskWarningModal
        reason={REASON}
        onConsent={vi.fn()}
        onCancel={vi.fn()}
        isPending={true}
      />,
    );

    const consentButton = screen.getByRole("button", { name: "동의하고 계속" });
    expect(consentButton).toBeDisabled();

    // Button 컴포넌트는 loading=true 시 spinning spinner span을 렌더함
    const spinner = document.querySelector("span.animate-spin");
    expect(spinner).toBeInTheDocument();
  });
});
