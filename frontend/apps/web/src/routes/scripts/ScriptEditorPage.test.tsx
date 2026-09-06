import "@testing-library/jest-dom/vitest";
import { ApiError } from "@aios/api-client";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScriptEditorPage, type CompileScript } from "./ScriptEditorPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  apiClient: { compileScript: vi.fn() },
}));

afterEach(cleanup);

function renderPage(compileScript: CompileScript) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ScriptEditorPage compileScript={compileScript} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const COMPILE_RESULT = {
  scriptHash: "a".repeat(64),
  grammarVersion: "aios-script-1",
  irVersion: "aios-ir-1",
  registryVersion: "reg-v1",
  irSha256: "b".repeat(64),
  instrCount: 5,
  resources: { seriesCount: 1, lookbackTotal: 14, opCount: 3, callCount: 1, callDepth: 1, plotCount: 1 },
  elapsedMs: 12,
};

describe("ScriptEditorPage", () => {
  it("컴파일 성공 시 소스를 그대로 넘기고 해시·산정치 미리보기를 보여준다", async () => {
    const compileScript = vi.fn(async (_source: string) => COMPILE_RESULT);
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.getByTestId("compile-preview-hash")).toBeInTheDocument());
    expect(compileScript.mock.calls[0]?.[0]).toEqual(expect.stringContaining("plot(rsi_val, 1)"));
    expect(screen.getByTestId("compile-preview-elapsed")).toHaveTextContent("12ms");
  });

  it("negative: 컴파일 오류(SCRIPT_SYNTAX)는 line/col 마커로 보여주고 일반 오류 배너는 그리지 않는다", async () => {
    const compileScript = vi.fn(async () => {
      throw new ApiError(400, "SCRIPT_SYNTAX: unexpected end of input", "trace-400", "VALIDATION_INVALID_FIELD", undefined, {
        code: "SCRIPT_SYNTAX",
        line: 1,
        col: 12,
      });
    });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() =>
      expect(screen.getByTestId("script-editor-marker-0")).toHaveTextContent("1행 12열: SCRIPT_SYNTAX"),
    );
    expect(screen.queryByText("입력값을 확인해주세요.")).not.toBeInTheDocument();
  });

  it("negative: 컴파일 무관 오류(403 AUTH_MFA_REQUIRED)는 마커 없이 매핑된 안내 문구를 보여준다", async () => {
    const compileScript = vi.fn(async () => {
      throw new ApiError(403, "raw server detail", "trace-403", "AUTH_MFA_REQUIRED");
    });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.getByText("추가 인증이 필요합니다.")).toBeInTheDocument());
    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("소스를 다시 편집하면 이전 컴파일 결과·오류가 초기화된다", async () => {
    const compileScript = vi.fn(async () => {
      throw new ApiError(400, "SCRIPT_SYNTAX: unexpected end of input", "trace-400", "VALIDATION_INVALID_FIELD", undefined, {
        code: "SCRIPT_SYNTAX",
        line: 1,
        col: 12,
      });
    });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
    await waitFor(() => expect(screen.getByTestId("script-editor-marker-0")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("script-editor-textarea"), { target: { value: "plot(close, 1)\n" } });

    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
  });

  it("CH-12: 컴파일 성공 시 plot 개수만큼 미리보기 서브패널을 보여준다", async () => {
    const compileScript = vi.fn(async () => ({ ...COMPILE_RESULT, resources: { ...COMPILE_RESULT.resources, plotCount: 2 } }));
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.getByTestId("script-preview-panes")).toBeInTheDocument());
    expect(screen.getByTestId("script-preview-pane-0")).toBeInTheDocument();
    expect(screen.getByTestId("script-preview-pane-1")).toBeInTheDocument();
    expect(screen.queryByTestId("script-preview-pane-2")).not.toBeInTheDocument();
  });

  it("CH-12: 소스를 편집하면 미리보기 서브패널이 사라진다", async () => {
    const compileScript = vi.fn(async () => COMPILE_RESULT);
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
    await waitFor(() => expect(screen.getByTestId("script-preview-panes")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("script-editor-textarea"), { target: { value: "plot(close, 1)\n" } });

    expect(screen.queryByTestId("script-preview-panes")).not.toBeInTheDocument();
  });

  it("CH-12: 다시 컴파일하면 이전 서브패널 대신 새 개수로 교체된다", async () => {
    const compileScript = vi
      .fn()
      .mockResolvedValueOnce({ ...COMPILE_RESULT, scriptHash: "a".repeat(64), resources: { ...COMPILE_RESULT.resources, plotCount: 2 } })
      .mockResolvedValueOnce({ ...COMPILE_RESULT, scriptHash: "b".repeat(64), resources: { ...COMPILE_RESULT.resources, plotCount: 1 } });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
    await waitFor(() => expect(screen.getByTestId("script-preview-pane-1")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.queryByTestId("script-preview-pane-1")).not.toBeInTheDocument());
    expect(screen.getByTestId("script-preview-pane-0")).toBeInTheDocument();
  });
});
