import "../../i18n";
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

// J7(docs/specs/UX_JOURNEYS.md §6) — 전략 저장 후 주소를 직접 쳐서 백테스트 화면으로
// 가지 않도록, 저장 성공 알림 옆에 다음 단계 CTA를 둔다. 대상 자산을 query로 넘겨
// 재입력 없이 ChartPage(instrument_id)가 바로 그 심볼로 열리게 한다.
describe("StrategyBuilderPage J7 백테스트 연결 CTA", () => {
  it("전략 저장 성공 시 백테스트로 이동 CTA가 대상 자산을 넘겨 /chart로 이동한다", async () => {
    saveMutateAsync.mockResolvedValue({
      strategyId: "my-strategy",
      version: "1.0.0",
      status: "draft",
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "백테스트로 이동 →" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "백테스트로 이동 →" }));

    expect(navigateMock).toHaveBeenCalledWith("/chart?instrument_id=BTC%2FUSDT");
  });

  it("negative: 저장 전에는 백테스트로 이동 CTA가 보이지 않는다", () => {
    renderPage();

    expect(screen.queryByRole("button", { name: "백테스트로 이동 →" })).not.toBeInTheDocument();
  });
});
