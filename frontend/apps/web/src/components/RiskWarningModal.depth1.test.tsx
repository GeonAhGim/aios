import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskWarningModal } from "./RiskWarningModal";

afterEach(() => cleanup());

const REASON = "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.";
const CONSENT = { name: "동의하고 계속" };

// 성능 예산: 로컬 실측 단일 렌더 ≈ 5ms(jsdom, i18n 로드 후). 30배 이상 여유로 400ms.
const RENDER_BUDGET_MS = 400;

function Harness({ onConsent }: { onConsent: () => Promise<void> }) {
  const [open, setOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  if (!open) return <p>closed</p>;
  return (
    <>
      {error ? <p role="alert">{error}</p> : null}
      <RiskWarningModal
        reason={REASON}
        onConsent={() => {
          onConsent().catch((e: Error) => setError(e.message));
        }}
        onCancel={() => setOpen(false)}
      />
    </>
  );
}

function timedRender(busyMs = 0): number {
  const start = performance.now();
  if (busyMs > 0) {
    while (performance.now() - start < busyMs) {
      /* injected latency */
    }
  }
  render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);
  return performance.now() - start;
}

describe("RiskWarningModal depth1 — negative", () => {
  it("정상 렌더에는 role=alert가 없다", () => {
    render(<RiskWarningModal reason={REASON} onConsent={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("Esc는 취소와 동일: 모달이 사라지고 onConsent는 호출되지 않는다", () => {
    const onConsent = vi.fn().mockResolvedValue(undefined);
    render(<Harness onConsent={onConsent} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onConsent).not.toHaveBeenCalled();
  });

  it("reason에 객체를 넘기면 React 렌더가 toThrow 한다", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() =>
      render(
        <RiskWarningModal
          reason={{ bad: 1 } as unknown as string}
          onConsent={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    ).toThrow();
    spy.mockRestore();
  });

  it("스크립트 문자열 reason은 텍스트로만 렌더되고 script 요소를 만들지 않는다", () => {
    const xss = '<script>alert("x")</script>';
    render(<RiskWarningModal reason={xss} onConsent={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(xss)).toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });
});

describe("RiskWarningModal depth1 — failure injection", () => {
  it("onConsent가 mockRejectedValue로 실패하면 모달은 유지되고 role=alert로 오류가 노출된다", async () => {
    const onConsent = vi.fn().mockRejectedValue(new Error("consent failed"));
    render(<Harness onConsent={onConsent} />);
    fireEvent.click(screen.getByRole("button", CONSENT));
    expect(await screen.findByRole("alert")).toHaveTextContent("consent failed");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onConsent).toHaveBeenCalledTimes(1);
  });

  it("실패 주입된 의존성 promise는 .rejects 로 원인을 보존하고 재시도는 다시 호출된다", async () => {
    const onConsent = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(undefined);
    await expect(onConsent()).rejects.toThrow("network error");
    render(<Harness onConsent={onConsent} />);
    fireEvent.click(screen.getByRole("button", CONSENT));
    await waitFor(() => expect(onConsent).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("RiskWarningModal depth1 — performance", () => {
  it("단일 렌더 경과시간이 예산(400ms) 미만이다", () => {
    const elapsed = timedRender();
    expect(elapsed).toBeLessThan(RENDER_BUDGET_MS);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("RiskWarningModal depth1 — gate red", () => {
  it("gate red: 지연을 주입하면 같은 성능 단언이 실제로 실패한다", () => {
    const elapsed = timedRender(RENDER_BUDGET_MS + 20);
    expect(() => expect(elapsed).toBeLessThan(RENDER_BUDGET_MS)).toThrow();
  });

  it("gate red: 입력을 망가뜨리면(reason 객체) 같은 렌더 단언이 실패한다", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const renderBroken = () =>
      render(
        <RiskWarningModal reason={{} as unknown as string} onConsent={vi.fn()} onCancel={vi.fn()} />,
      );
    expect(renderBroken).toThrow();
    spy.mockRestore();
    expect(() => expect(screen.getByRole("dialog")).toBeInTheDocument()).toThrow();
  });
});
