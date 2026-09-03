import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { StrategyBuilderPage } from "./StrategyBuilderPage";

const previewMutateAsync = vi.fn();
const saveMutateAsync = vi.fn();
const generateWizardMutateAsync = vi.fn();
const generateFromPromptMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useIndicators: () => ({ data: { indicators: ["RSI", "SMA", "EMA"] } }),
  useCandles: () => ({ data: [], isError: false }),
  useCreateStrategy: () => ({ mutateAsync: saveMutateAsync, isPending: false }),
  usePreviewStrategy: () => ({ mutateAsync: previewMutateAsync, isPending: false, data: undefined }),
  useGenerateWizardStrategy: () => ({ mutateAsync: generateWizardMutateAsync, isPending: false }),
  useGenerateFromPrompt: () => ({ mutateAsync: generateFromPromptMutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  previewMutateAsync.mockReset();
  saveMutateAsync.mockReset();
  generateWizardMutateAsync.mockReset();
  generateFromPromptMutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <StrategyBuilderPage />
    </MemoryRouter>,
  );
}

// task-929 §3.3: 미리보기·저장 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("StrategyBuilderPage 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    saveMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 미리보기 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    previewMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));

    await waitFor(() => expect(screen.getByText("미리보기에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("전략 ID 미입력은 여전히 클라이언트 검증 문구를 보여준다", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByText("전략 ID를 입력해주세요.")).toBeInTheDocument(),
    );
    expect(saveMutateAsync).not.toHaveBeenCalled();
  });
});
