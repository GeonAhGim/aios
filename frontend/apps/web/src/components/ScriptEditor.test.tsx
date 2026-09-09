import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScriptEditor } from "./ScriptEditor";

afterEach(cleanup);

function gutterLine(container: HTMLElement, lineNumber: number): HTMLElement {
  const gutter = within(container).getByTestId("script-editor-gutter");
  const line = Array.from(gutter.children).find((el) => el.textContent === String(lineNumber));
  if (!line) {
    throw new Error(`gutter line ${lineNumber} not found`);
  }
  return line as HTMLElement;
}

describe("ScriptEditor", () => {
  it("markers 배열의 항목 수만큼 마커를 그리고 해당 줄 번호를 강조한다", () => {
    render(
      <ScriptEditor
        value={"line one\nline two\nline three"}
        onChange={vi.fn()}
        markers={[
          { line: 1, col: 3, message: "SCRIPT_SYNTAX" },
          { line: 3, col: 1, message: "SCRIPT_TYPE" },
        ]}
      />,
    );

    expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("1행 3열: SCRIPT_SYNTAX");
    expect(screen.getByTestId("script-editor-marker-1")).toHaveTextContent("3행 1열: SCRIPT_TYPE");
  });

  it("negative 1/3: markers가 빈 배열이면 마커 목록을 렌더하지 않는다", () => {
    render(<ScriptEditor value="plot(close, 1)" onChange={vi.fn()} markers={[]} />);

    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
  });

  it("negative 2/3: 같은 줄에 마커가 두 개 겹쳐도 둘 다 목록에 그리고 거터는 해당 줄에 한 번만 강조 클래스를 붙인다", () => {
    const { container } = render(
      <ScriptEditor
        value={"a\nb\nc"}
        onChange={vi.fn()}
        markers={[
          { line: 2, col: 1, message: "SCRIPT_SYNTAX" },
          { line: 2, col: 4, message: "SCRIPT_TYPE" },
        ]}
      />,
    );

    // 컴파일러가 한 줄에서 여러 진단을 냈다고 백엔드가 배열을 보내는 경우를
    // 재현한다(DSL-13b compile 라우터가 details 배열을 확장하면 이 형태가 됨).
    expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("2행 1열: SCRIPT_SYNTAX");
    expect(screen.getByTestId("script-editor-marker-1")).toHaveTextContent("2행 4열: SCRIPT_TYPE");
    const line2 = gutterLine(container, 2);
    expect(line2).toHaveClass("text-danger");
    // 강조 클래스가 중복 부여되어 스타일이 깨지지 않는지도 함께 확인한다.
    expect(line2.className.split(" ").filter((c) => c === "text-danger")).toHaveLength(1);
  });

  it("negative 3/3: line/col이 0 이하이거나 실제 줄 수를 벗어나도 렌더가 깨지지 않고 거터 강조는 되지 않는다", () => {
    const { container } = render(
      <ScriptEditor
        value={"only-one-line"}
        onChange={vi.fn()}
        markers={[
          { line: 0, col: -1, message: "SCRIPT_SYNTAX" },
          { line: 99, col: 0, message: "SCRIPT_TYPE" },
        ]}
      />,
    );

    expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("0행 -1열: SCRIPT_SYNTAX");
    expect(screen.getByTestId("script-editor-marker-1")).toHaveTextContent("99행 0열: SCRIPT_TYPE");
    // value는 한 줄뿐이라 거터는 "1" 한 칸만 존재하고, 범위 밖 line 값은
    // 어떤 거터 행과도 매칭되지 않아 강조 클래스가 전혀 붙지 않는다.
    const gutter = within(container).getByTestId("script-editor-gutter");
    expect(gutter.children).toHaveLength(1);
    expect(gutter.children[0]).not.toHaveClass("text-danger");
  });

  it("failure-injection: 컴파일 API가 message 없이 실패 payload를 보내도(빈 문자열·NaN col) 예외 없이 렌더한다", () => {
    // /v1/scripts/compile이 부분적으로 망가진 오류 봉투(details.message 누락,
    // col이 숫자가 아닌 NaN으로 직렬화되는 등)를 돌려주는 백엔드 결함을 주입한다.
    expect(() =>
      render(
        <ScriptEditor
          value={"plot(close)"}
          onChange={vi.fn()}
          markers={[{ line: 1, col: Number.NaN, message: "" }]}
        />,
      ),
    ).not.toThrow();

    expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("1행 NaN열:");
  });

  it("성능: 1000줄·200마커짜리 대형 스크립트도 5s 안에 렌더한다(마커당 O(1) 매핑 회귀 가드)", () => {
    const value = Array.from({ length: 1000 }, (_, i) => `line_${i}`).join("\n");
    const markers = Array.from({ length: 200 }, (_, i) => ({
      line: (i * 5) + 1,
      col: 1,
      message: `SCRIPT_ERROR_${i}`,
    }));

    const start = performance.now();
    render(<ScriptEditor value={value} onChange={vi.fn()} markers={markers} rows={20} />);
    const elapsed = performance.now() - start;

    expect(screen.getByTestId("script-editor-marker-199")).toHaveTextContent("SCRIPT_ERROR_199");
    // PortfolioPage.test.tsx의 40장 카드 케이스와 동일한 관용(15s급) 예산 안에서,
    // O(n^2)로 퇴행하면(1000줄 x 200마커) 수 초를 넘어가므로 그 전에 잡힌다.
    expect(elapsed).toBeLessThan(5000);
  });

  it("게이트 적색 재현: line/col/message가 완전히 동일한 마커 두 개가 와도 React key 충돌로 하나가 사라지지 않는다", () => {
    // 마커 key는 `${line}:${col}:${index}`로 index를 포함한다(ScriptEditor.tsx).
    // 컴파일러가 같은 좌표에서 동일 메시지를 두 번 내보내는 경우(예: 재컴파일
    // 응답을 합치다 중복 삽입) index 없이 `${line}:${col}`만 key로 쓰면 React가
    // 동일 key로 인식해 두 번째 항목을 리스트에서 지워버린다. 이 테스트는 그
    // 회귀가 재도입되면 marker-1을 찾지 못해 실제로 적색이 되어야 한다.
    render(
      <ScriptEditor
        value={"a\nb"}
        onChange={vi.fn()}
        markers={[
          { line: 2, col: 1, message: "SCRIPT_SYNTAX" },
          { line: 2, col: 1, message: "SCRIPT_SYNTAX" },
        ]}
      />,
    );

    expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("2행 1열: SCRIPT_SYNTAX");
    expect(screen.getByTestId("script-editor-marker-1")).toHaveTextContent("2행 1열: SCRIPT_SYNTAX");
  });
});
