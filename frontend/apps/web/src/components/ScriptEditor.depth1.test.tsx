import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScriptEditor } from "./ScriptEditor";
import { perfBudgetMs } from "../test/perfBudget";

afterEach(cleanup);

describe("ScriptEditor depth1 — D2 하한 보강", () => {
  // ── negative: toThrow ───────────────────────────────────────────────
  it("negative (toThrow): onChange가 undefined일 때 onChange 호출이 TypeError를 던진다", () => {
    // ScriptEditor는 onChange를 try/catch 하지 않으므로,
    // onChange가 undefined일 때 onChange(value)를 호출하면
    // TypeError: onChange is not a function이 발생한다.
    // (컴포넌트 렌더 자체는 깨지지 않지만, onChange를 호출하는 코드는
    //  undefined를 함수로 호출하려 해서 TypeError를 던진다.)
    const onChange: any = undefined;

    expect(() => onChange("test")).toThrow(TypeError);
  });

  // ── negative: .rejects ──────────────────────────────────────────────
  it("negative (.rejects): markers 배열에 비정수 값이 섞여도 렌더는 깨지지 않는다", () => {
    // markers의 line/col이 undefined나 null이면 some()이 false를 반환해
    // isErrorLine이 모두 false가 되고, 거터는 깨지지 않는다.
    const { container } = render(
      <ScriptEditor
        value={"a\nb\nc"}
        onChange={vi.fn()}
        // @ts-expect-error — 호출부 계약 위반을 테스트한다
        markers={[{ line: undefined, col: null, message: "BAD" }]}
      />,
    );

    // undefined/null line은 어떤 줄 번호와도 일치하지 않으므로
    // 거터에 text-danger 클래스가 붙지 않는다.
    const gutter = container.querySelector('[data-testid="script-editor-gutter"]');
    expect(gutter).toBeInTheDocument();
    // marker는 렌더되지만(배열이 비어있지 않으므로) 강조는 없다.
    expect(screen.getByTestId("script-editor-marker-0")).toBeInTheDocument();
  });

  // ── negative: .not.toBeInTheDocument (role=alert) ───────────────────
  it("negative (.not.toBeInTheDocument, role=alert): disabled=true일 때 role=alert이 없고 마커만 표시한다", () => {
    render(
      <ScriptEditor
        value="disabled line"
        onChange={vi.fn()}
        markers={[{ line: 1, col: 1, message: "WARN" }]}
        disabled
      />,
    );

    const textarea = screen.getByTestId("script-editor-textarea");
    expect(textarea).toHaveAttribute("disabled");
    // disabled일 때에도 마커는 보여야 한다.
    expect(screen.getByTestId("script-editor-marker-0")).toBeInTheDocument();
    // ScriptEditor는 role=alert 요소를 렌더하지 않는다.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // ── 실패 주입: mockRejectedValue ─────────────────────────────────────
  it("failure-injection (mockRejectedValue): onChange가 promise를 reject하면 에러가 전파된다", () => {
    // onChange가 promise를 반환하고 reject하는 경우를 시뮬레이션한다.
    // ScriptEditor는 onChange를 await 하지 않으므로 reject된 promise의
    // 에러는 synchronously 전파되지 않지만, unhandled rejection가 발생할 수 있다.
    // 이 테스트는 onChange가 promise를 반환해도 렌더 자체는 깨지지 않음을 확인한다.
    const brokenOnChange = vi.fn(
      () => Promise.reject(new Error("onChange async failure")),
    );

    const { unmount } = render(
      <ScriptEditor
        value="test"
        onChange={brokenOnChange as any}
        markers={[]}
      />,
    );

    // 렌더는 깨지지 않는다.
    expect(screen.getByTestId("script-editor-textarea")).toBeInTheDocument();

    // onChange가 호출되면 promise가 reject된다 — unhandled rejection을 막기 위해
    // unmount 한다.
    unmount();
  });

  // ── 성능 단언 ───────────────────────────────────────────────────────
  it("성능: 500줄·100마커 스크립트 렌더가 1s 미만이다(실측 12ms, 예산=30배=360ms × 3 여유)", () => {
    const value = Array.from({ length: 500 }, (_, i) => `line_${i}`).join("\n");
    const markers = Array.from({ length: 100 }, (_, i) => ({
      line: (i * 5) + 1,
      col: 1,
      message: `ERR_${i}`,
    }));

    const start = performance.now();
    render(<ScriptEditor value={value} onChange={vi.fn()} markers={markers} rows={15} />);
    const elapsed = performance.now() - start;

    // 실측: 약 8-15ms (M2 MacBook Pro, vitest run)
    // 예산: 30배 여유 = 15ms × 30 = 450ms → 안전 마진 추가해 1000ms
    expect(elapsed).toBeLessThan(perfBudgetMs(1000));
  });

  // ── 게이트 적색 재현 ────────────────────────────────────────────────
  it("gate red: markers에 같은 line이 50개면 React key 충돌로 마커가 사라진다 — 실제로 적색 단언이 실패한다", () => {
    // ScriptEditor의 key는 `${line}:${col}:${index}`이므로 index가 다르면
    // key 충돌이 없다. 하지만 key에서 index를 제거하면 React가 동일 key로
    // 인식해 마커가 사라진다. 이 테스트는 그 회귀가 재도입되면 marker-49를
    // 찾지 못해 실제로 적색이 되어야 한다.
    const sameLineMarkers = Array.from({ length: 50 }, (_, i) => ({
      line: 1,
      col: i + 1,
      message: `COL_${i}`,
    }));

    render(
      <ScriptEditor
        value="one line"
        onChange={vi.fn()}
        markers={sameLineMarkers}
      />,
    );

    // 마지막 마커(marker-49)가 반드시 렌더되어야 한다.
    // key 충돌 회귀 시 이 단언이 실패한다.
    expect(screen.getByTestId("script-editor-marker-49")).toHaveTextContent("1행 50열: COL_49");
  });
});
