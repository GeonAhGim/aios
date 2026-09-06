import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScriptEditor } from "./ScriptEditor";

afterEach(cleanup);

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

  it("negative: markers가 빈 배열이면 마커 목록을 렌더하지 않는다", () => {
    render(<ScriptEditor value="plot(close, 1)" onChange={vi.fn()} markers={[]} />);

    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
  });
});
