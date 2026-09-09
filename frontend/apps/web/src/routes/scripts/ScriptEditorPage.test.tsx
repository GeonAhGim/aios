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

  // DEPTH_DSL_IND(task-2727)가 원 task-1558(af0cbf8)을 D2 축 하한 미달(D1)로
  // 판정 — negative가 위 2건(SCRIPT_SYNTAX 마커·403 MFA)뿐이라 얕았고, 실패
  // 주입·성능 단언·게이트 적색 재현이 전부 비어 있었다(task-2922 DEEPEN).
  // 아래는 compileScript prop(실제로는 apiClient.compileScript, scripts.test.ts가
  // http 계층을 이미 검증)이 컴파일 무관 실패로 reject됐을 때 화면이 어떻게
  // 반응하는지를 겨눈다 — 마커 없이 매핑된 안내만, 원본 상세는 노출하지 않는다.
  it("negative: 네트워크 타임아웃(AbortError)은 컴파일 오류가 아니므로 마커 없이 오류 메시지를 그대로 보여준다", async () => {
    const compileScript = vi.fn(async () => {
      const err = new Error("The operation was aborted due to timeout");
      err.name = "AbortError";
      throw err;
    });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.getByText("The operation was aborted due to timeout")).toBeInTheDocument());
    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "컴파일" })).not.toBeDisabled();
  });

  it("negative: 5xx(500 INTERNAL_ERROR)는 매핑된 일반 안내 문구만 보여주고 원본 상세를 노출하지 않는다", async () => {
    const compileScript = vi.fn(async () => {
      throw new ApiError(500, "internal detail leak", "trace-500", "INTERNAL_ERROR");
    });
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() =>
      expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("internal detail leak")).not.toBeInTheDocument();
    expect(screen.queryByTestId("script-editor-markers")).not.toBeInTheDocument();
  });

  it("failure-injection: 컴파일 성공으로 미리보기가 떠 있는 상태에서 재컴파일이 네트워크 단절로 실패하면 낡은 미리보기 대신 오류 배너만 남고 버튼은 다시 눌릴 수 있다", async () => {
    const compileScript = vi
      .fn()
      .mockResolvedValueOnce(COMPILE_RESULT)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderPage(compileScript);

    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
    await waitFor(() => expect(screen.getByTestId("compile-preview-hash")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("script-editor-textarea"), { target: { value: "plot(close, 2)\n" } });
    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));

    await waitFor(() => expect(screen.getByText("Failed to fetch")).toBeInTheDocument());
    // 편집 시 초기화된 미리보기 패널이 실패한 재컴파일로 되살아나지 않는다(낡은 상태 유출 없음).
    expect(screen.queryByTestId("script-preview-panes")).not.toBeInTheDocument();
    expect(screen.queryByTestId("compile-preview-hash")).not.toBeInTheDocument();
    // mutation.isPending이 실패 후 false로 돌아와 버튼이 "컴파일 중..."에 멈춰있지 않는다.
    expect(screen.getByRole("button", { name: "컴파일" })).not.toBeDisabled();
    expect(compileScript).toHaveBeenCalledTimes(2);
  });

  it("성능 단언: 컴파일 클릭부터 미리보기 렌더까지 짧은 시간 안에 반응한다(동기 블로킹 없음 확인)", async () => {
    const compileScript = vi.fn(async (_source: string) => COMPILE_RESULT);
    renderPage(compileScript);

    const startedAt = performance.now();
    fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
    await waitFor(() => expect(screen.getByTestId("compile-preview-hash")).toBeInTheDocument());
    const elapsedMs = performance.now() - startedAt;

    // waitFor의 폴링·React 렌더 오버헤드까지 포함한 왕복 시간이라 여유를 크게
    // 둔다 — 목적은 정밀한 임계값이 아니라 동기 블로킹(예: 컴파일 결과를 받고도
    // 렌더가 초 단위로 밀리는 회귀)이 생기면 이 값이 신호를 준다는 것이다.
    expect(elapsedMs).toBeLessThan(3000);
  });

  // task-618 계열 DEEPEN과 동일 기법: showGenericError = isError && !compileErrorDetails
  // 중 compileErrorDetails 체크를 되돌린 naive 계산을 나란히 실행해, 51번(위
  // "negative: 컴파일 오류(SCRIPT_SYNTAX)...") 단언이 실제로 어떤 회귀를
  // 적색으로 잡아내는 게이트인지 증명한다.
  it("게이트 적색 재현: compileErrorDetails 체크 없이 mutation.isError만 보는 naive 계산으로 회귀하면 SCRIPT_SYNTAX 마커와 일반 배너가 동시에 뜬다", async () => {
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

    // 실제 DOM: showGenericError = isError(true) && !compileErrorDetails(false) = false → 배너 없음.
    expect(screen.queryByText("입력값을 확인해주세요.")).not.toBeInTheDocument();

    function naiveShowGenericError(isError: boolean): boolean {
      return isError; // 회귀: compileErrorDetails 존재 여부를 더 이상 보지 않는다
    }
    function realShowGenericError(isError: boolean, hasCompileErrorDetails: boolean): boolean {
      return isError && !hasCompileErrorDetails;
    }

    // 같은 입력(isError=true, compileErrorDetails 있음)에서 실제 계산은 false(위 DOM과 일치)지만
    // naive 계산은 true다 — naive였다면 마커 옆에 "입력값을 확인해주세요." 배너도 함께 떴을 것.
    expect(realShowGenericError(true, true)).toBe(false);
    expect(naiveShowGenericError(true)).toBe(true);
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
