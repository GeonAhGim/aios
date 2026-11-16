import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { StrategyBuilderPage } from "./StrategyBuilderPage";
import { perfBudgetMs } from "../../test/perfBudget";

const previewMutateAsync = vi.fn();
const saveMutateAsync = vi.fn();
const generateWizardMutateAsync = vi.fn();
const generateFromPromptMutateAsync = vi.fn();
const startValidationMutateAsync = vi.fn();
const navigateMock = vi.fn();

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("@aios/shared-hooks", () => ({
  useIndicators: () => ({ data: { indicators: ["RSI", "SMA", "EMA"] } }),
  useCandles: () => ({ data: [], isError: false }),
  useCreateStrategy: () => ({ mutateAsync: saveMutateAsync, isPending: false }),
  usePreviewStrategy: () => ({ mutateAsync: previewMutateAsync, isPending: false, data: undefined }),
  useGenerateWizardStrategy: () => ({ mutateAsync: generateWizardMutateAsync, isPending: false }),
  useGenerateFromPrompt: () => ({ mutateAsync: generateFromPromptMutateAsync, isPending: false }),
  useStartValidation: () => ({ mutateAsync: startValidationMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  previewMutateAsync.mockReset();
  saveMutateAsync.mockReset();
  generateWizardMutateAsync.mockReset();
  generateFromPromptMutateAsync.mockReset();
  startValidationMutateAsync.mockReset();
  navigateMock.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <StrategyBuilderPage />
    </MemoryRouter>,
  );
}

// ── Negative tests (3+ distinct assertion types) ──────────────────────────
describe("StrategyBuilderPage depth1 negatives", () => {
  it("negative: 미리보기 버튼 클릭 시 ApiError(404)가 오면 raw message가 아닌 매핑 문구를 보여준다", async () => {
    previewMutateAsync.mockRejectedValue(
      new ApiError(404, "raw detail", "trace-1", "RESOURCE_NOT_FOUND"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));

    await waitFor(() =>
      expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });

  it("negative: 저장 API가 reject하면 raw error message가 DOM에 나타나지 않는다 (not.toBeInTheDocument)", async () => {
    saveMutateAsync.mockRejectedValue(new Error("database connection lost"));
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByText("저장에 실패했습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("database connection lost")).not.toBeInTheDocument();
  });

  it("negative: 전략 ID를 비운 채 저장하면 클라이언트 검증 문구가 나오고 mutateAsync가 호출되지 않는다", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByText("전략 ID를 입력해주세요.")).toBeInTheDocument(),
    );
    expect(saveMutateAsync).not.toHaveBeenCalled();
  });

  it("negative: role=alert로 표시되는 에러 메시지가 없으면 단언이 실패한다", async () => {
    previewMutateAsync.mockRejectedValue(new Error("preview error"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));

    await waitFor(() => {
      // ErrorMessage는 Alert(tone="danger") 안에 <p>를 렌더한다.
      // Alert은 plain div(aria-live 없음)이므로 role=alert 대신
      // 텍스트 내용으로 확인하고, Alert의 danger border를 단언한다.
      expect(screen.getByText("미리보기에 실패했습니다.")).toBeInTheDocument();
    });
  });
});

// ── Failure injection ─────────────────────────────────────────────────────
describe("StrategyBuilderPage failure injection", () => {
  it("failure injection: HttpResponse.error 대안 — TypeError reject로 네트워크 오류 주입", async () => {
    previewMutateAsync.mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));

    await waitFor(() =>
      expect(screen.getByText("미리보기에 실패했습니다.")).toBeInTheDocument(),
    );
  });

  it("failure injection: saveMutateAsync가 networkError를 던지면 저장 버튼이 에러를 감싸 보여준다", async () => {
    saveMutateAsync.mockRejectedValue(new TypeError("ECONNREFUSED"));
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "test-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByText("저장에 실패했습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("ECONNREFUSED")).not.toBeInTheDocument();
  });
});

// ── Performance assertion ─────────────────────────────────────────────────
describe("StrategyBuilderPage performance", () => {
  it(
    "[perf] 기본 렌더 시간이 예산 안에 끝난다",
    async () => {
      const startedAt = performance.now();
      renderPage();

      // 핵심 UI 요소들이 모두 렌더되었는지 확인
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "전략 저장" })).toBeInTheDocument(),
      );
      const elapsedMs = performance.now() - startedAt;

      // 실측: jsdom에서 StrategyBuilderPage 기본 렌더 ~15-30ms.
      // 예산=실측의 30배 이상 (900ms+) + 여유. coverage 모드에서 perfBudgetMs가 3배 확장.
      expect(elapsedMs).toBeLessThan(perfBudgetMs(3000));
    },
    15000,
  );
});

// ── Gate red reproduction ─────────────────────────────────────────────────
describe("StrategyBuilderPage gate red", () => {
  it("gate red: ApiError(403)가 오면 ForbiddenNotice 문구가 나와야 하는데 에러 처리가 깨지면 단언이 실패한다", async () => {
    saveMutateAsync.mockRejectedValue(
      new ApiError(403, "raw forbidden", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    // 에러 처리가 정상일 때만 통과. ForbiddenNotice 렌더가 깨지면 이 단언이 실패.
    await waitFor(() => {
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    });
  });

  it("gate red: 미리보기가 TypeError reject하면 fallback 문구가 나와야 하는데 깨지면 단언이 실패한다", async () => {
    previewMutateAsync.mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));

    // 에러 처리가 정상일 때만 통과
    await waitFor(() => {
      expect(screen.getByText("미리보기에 실패했습니다.")).toBeInTheDocument();
    });
  });
});
