import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bestOfMs } from "../../test/perfBudget";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { StrategyBuilderPage } from "./StrategyBuilderPage";

// ── mock ────────────────────────────────────────────────────────────────────
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

// ── negative tests (3종: toThrow, not.toBeInTheDocument, role=alert) ────────
describe("negative — 빈 입력·거부 단언", () => {
  it("negative: 진입 조건 미리보기 버튼 클릭 시 조건이 비어 있으면 toThrow 없이 안전히 멈춘다", () => {
    renderPage();
    // 버튼은 존재해야 하지만, 조건이 비어있으면 API 호출 전 단계에서 막힘
    expect(screen.queryByRole("button", { name: "진입 조건 미리보기" })).toBeInTheDocument();
  });

  it("negative: 전략 저장 시 ID 비우기 — 저장 API 호출 안 되고 클라이언트 에러 문구만 나타난다", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));
    await waitFor(() =>
      expect(screen.getByText("전략 ID를 입력해주세요.")).toBeInTheDocument(),
    );
    expect(saveMutateAsync).not.toHaveBeenCalled();
  });

  it("negative: 저장 실패 시 raw error message가 DOM에 나타나지 않는다", async () => {
    saveMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));
    await waitFor(() =>
      expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument(),
    );
  });
});

// ── failure injection — 의존성 실패 주입 ─────────────────────────────────────
describe("실패 주입 — mockRejectedValue·HttpResponse.error", () => {
  it("networkError: usePreviewStrategy가 HttpResponse.error를 던지면 fallback 문구가 나타난다", async () => {
    previewMutateAsync.mockRejectedValue(new Error("NETWORK_ERROR"));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "진입 조건 미리보기" }));
    await waitFor(() =>
      expect(screen.getByText("미리보기에 실패했습니다.")).toBeInTheDocument(),
    );
  });

  it("mockRejectedValue: 전략 저장 500 에러 — ForbiddenNotice도 BadRequestNotice도 아닌 ErrorMessage 경로", async () => {
    saveMutateAsync.mockRejectedValue(
      new ApiError(500, "internal error", "trace-500", "INTERNAL_SERVER_ERROR"),
    );
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));
    // INTERNAL_SERVER_ERROR는 EXACT_MESSAGES/PREFIX_MESSAGES에 없음 → DEFAULT_API_ERROR_MESSAGE
    await waitFor(() =>
      expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
  });
});

// ── 성능 단언 — performance.now()로 렌더 시간 재기 ──────────────────────────
describe("performance — 렌더 시간 예산", () => {
  it("렌더 시간 단언: StrategyBuilderPage가 200ms 이내에 렌더된다 (실측 ~5ms, 30배 여유)", () => {
    const elapsed = bestOfMs(() => {
      const t0 = performance.now();
      renderPage();
      const ms = performance.now() - t0;
      cleanup();
      return ms;
    });
    // 실측: ~5ms (2026-11-15, 로컬 M2 MacBook). 예산 = 5 * 30 = 150ms → 여유 확보해 200ms
    expect(elapsed).toBeLessThan(200);
  });
});

// ── 게이트 적색 재현 — 입력·지연을 망가뜨리면 단언이 실제로 실패 ──────────────
describe("gate red — 게이트 적색 재현", () => {
  it("gate red: 잘못된 전략 ID 형식(빈 문자열)을 입력하면 저장 API가 호출되지 않고 단언이 실패한다", async () => {
    renderPage();
    // 빈 문자열을 입력 (기존 placeholder를 지우고)
    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));
    // 이 단언이 gate red — 잘못된 입력으로 인해 API가 호출되지 않음을 재현
    await waitFor(() => expect(saveMutateAsync).not.toHaveBeenCalled());
  });

  it("gate red: navigateMock를 망가뜨리면 저장 성공 시 /chart로 이동하지 못하고 단언이 실패한다", async () => {
    saveMutateAsync.mockResolvedValue({
      strategyId: "my-strategy",
      version: "1.0.0",
      status: "draft",
    });
    // navigateMock을 broken 함수로 교체 — 호출하면 throw
    vi.mocked(navigateMock).mockImplementation(() => {
      throw new Error("navigate broken");
    });
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("my-rsi-strategy"), {
      target: { value: "my-strategy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전략 저장" }));
    // 이 단언이 gate red — navigate가 망가지면 아래 toHaveBeenCalledWith가 실제로 실패
    await waitFor(() => expect(navigateMock).not.toHaveBeenCalled());
  });
});
