import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskWarningModal } from "./RiskWarningModal";

afterEach(() => cleanup());

const REASON = "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.";

// task-1198: 핸들러가 호출됐는지만 보는 것은 컴포넌트가 props를 그대로
// 위임한다는 사실의 동어반복이다 — 아래는 실제 사용 방식(부모가 open
// 상태·isPending 상태를 소유하고 콜백으로 갱신)을 그대로 재현한 하네스로
// DOM에 남는/사라지는 결과와 버튼의 disabled 속성을 단언한다.
function ConsentHarness({ onConsent }: { onConsent: () => void }) {
  const [open, setOpen] = useState(true);
  if (!open) return <p>closed</p>;
  return (
    <RiskWarningModal
      reason={REASON}
      onConsent={onConsent}
      onCancel={() => setOpen(false)}
    />
  );
}

function DoubleSubmitHarness({ onConsent }: { onConsent: () => void }) {
  const [isPending, setIsPending] = useState(false);
  return (
    <RiskWarningModal
      reason={REASON}
      isPending={isPending}
      onConsent={() => {
        setIsPending(true);
        onConsent();
      }}
      onCancel={() => {}}
    />
  );
}

describe("RiskWarningModal", () => {
  it("렌더 직후에는 onConsent가 호출되지 않고, 동의 버튼을 눌러야 정확히 한 번 호출된다", () => {
    const onConsent = vi.fn();
    render(<ConsentHarness onConsent={onConsent} />);

    expect(onConsent).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "동의하고 계속" }));
    expect(onConsent).toHaveBeenCalledTimes(1);
  });

  it("취소를 누르면 모달이 DOM에서 사라지고 onConsent는 끝까지 호출되지 않는다", () => {
    const onConsent = vi.fn();
    render(<ConsentHarness onConsent={onConsent} />);

    expect(screen.getByText("위험등급 불일치 경고")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByText("위험등급 불일치 경고")).not.toBeInTheDocument();
    expect(screen.getByText("closed")).toBeInTheDocument();
    expect(onConsent).not.toHaveBeenCalled();
  });

  it("isPending 중에는 동의 버튼이 비활성화되어 연속 클릭해도 onConsent가 한 번만 호출된다", () => {
    const onConsent = vi.fn();
    render(<DoubleSubmitHarness onConsent={onConsent} />);

    const button = screen.getByRole("button", { name: "동의하고 계속" });
    expect(button).not.toBeDisabled();

    fireEvent.click(button);
    expect(onConsent).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();

    fireEvent.click(button);
    expect(onConsent).toHaveBeenCalledTimes(1);
  });

  it("취소 버튼은 isPending과 무관하게 항상 활성화 상태다", () => {
    render(
      <RiskWarningModal reason={REASON} isPending onConsent={vi.fn()} onCancel={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "취소" })).not.toBeDisabled();
  });

  // UX-4 task-2688: role="dialog"/aria-labelledby + 포커스 트랩 baseline 증빙
  // (AlertFromChart.test.tsx의 동형 테스트와 같은 계약).
  it("dialog 역할과 제목에 연결된 aria-labelledby를 갖는다", () => {
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(labelledBy).toBeTruthy();
    expect(document.getElementById(labelledBy!)).toHaveTextContent("위험등급 불일치 경고");
  });

  it("Esc를 누르면 onCancel이 호출된다(onConsent는 호출되지 않는다)", () => {
    const onConsent = vi.fn();
    const onCancel = vi.fn();
    render(<RiskWarningModal reason={REASON} onConsent={onConsent} onCancel={onCancel} />);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConsent).not.toHaveBeenCalled();
  });

  it("[negative] Tab 포커스는 다이얼로그 안에서만 순환한다(포커스 트랩)", () => {
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);

    const dialog = screen.getByRole("dialog");
    const focusable = dialog.querySelectorAll<HTMLElement>("button:not(:disabled)");
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});
