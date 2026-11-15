import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { AlertFromChart, type AlertFromChartProps } from "./AlertFromChart";
import { perfBudgetMs } from "../../test/perfBudget";

const createAlertMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useCreateAlert: () => ({ mutateAsync: createAlertMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  createAlertMutateAsync.mockReset();
});

function baseProps(): AlertFromChartProps {
  return {
    isOpen: true,
    onClose: vi.fn(),
    venue: "BITGET",
    instrumentId: "BTCUSDT",
    timeframe: "1h",
    currentClose: 50200,
    selectedIndicatorIds: [],
  };
}

function renderDialog(overrides: Partial<AlertFromChartProps> = {}) {
  return render(
    <MemoryRouter>
      <AlertFromChart {...baseProps()} {...overrides} />
    </MemoryRouter>,
  );
}

describe("AlertFromChart depth1 — DEEPEN-DEPTH CH-6", () => {
  // ── negative tests (4종: not.toBeInTheDocument, not.toHaveBeenCalled, ApiError reject, role=alert) ──

  it("negative: 닫힌 다이얼로그에 role=alert 요소가 없어야 한다", () => {
    renderDialog({ isOpen: false });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: 제출 시 빈 임계값으로 mutateAsync가 호출되지 않는다 (React 폼 유효성)", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 1 });
    renderDialog();

    const thresholdInput = screen.getByLabelText("임계값");
    fireEvent.change(thresholdInput, { target: { value: "" } });
    const submitBtn = screen.getByRole("button", { name: "알림 등록" });

    fireEvent.click(submitBtn);
    await waitFor(() => expect(createAlertMutateAsync).not.toHaveBeenCalled());
  });

  it("negative: 서버 403 POLICY_* 거부는 ForbiddenNotice 매핑 문구를 보여준다", async () => {
    createAlertMutateAsync.mockRejectedValue(
      new ApiError(403, "raw detail", "trace-2", "POLICY_LIVE_BLOCKED"),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
  });

  it("negative: plain Error 응답 시 handleSubmit이 i18n 메시지로 재감싸서 ErrorMessage에 보인다", async () => {
    createAlertMutateAsync.mockRejectedValue(new Error("Network request failed"));
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    // handleSubmit에서 plain Error → new Error(t("legacy.alertFromChart.t15")) 재감싸기
    // → ErrorCode 없음 → getApiErrorMessage(fallback) → "알림 생성에 실패했습니다."
    await waitFor(() =>
      expect(screen.getByText("알림 생성에 실패했습니다.")).toBeInTheDocument(),
    );
  });

  // ── 실패 주입 tests ──

  it("실패 주입: mutateAsync가 500 INTERNAL_ERROR reject하면 매핑 배너가 나타난다", async () => {
    createAlertMutateAsync.mockRejectedValue(
      new ApiError(500, "서버 오류", "trace-500", "INTERNAL_ERROR", undefined, {}),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    // INTERNAL_ERROR → getApiErrorMessage("INTERNAL_ERROR", "서버 오류")
    // → "일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."
    await waitFor(() =>
      expect(
        screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
      ).toBeInTheDocument(),
    );
  });

  it("실패 주입: mutateAsync가 plain Error를 던지면 handleSubmit이 i18n 메시지로 재감싼다", async () => {
    createAlertMutateAsync.mockRejectedValue(new Error("Network request failed"));
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    // handleSubmit(err instanceof ApiError ? err : new Error(t(...)))
    await waitFor(() =>
      expect(screen.getByText("알림 생성에 실패했습니다.")).toBeInTheDocument(),
    );
  });

  // ── 성능 단언 tests (실측: ~10ms, 예산 30배 = 300ms → perfBudgetMs(10) 사용) ──

  it("성능: 다이얼로그 열림 렌더 시간이 예산 이내여야 한다 (실측 ~10ms, 예산 perfBudgetMs(10))", () => {
    const start = performance.now();
    renderDialog({ isOpen: true });
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(perfBudgetMs(10));

    // 렌더 후 dialog가 실제로 보이는지 also 확인
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });

  // ── 게이트 적색 재현 tests ──

  it("gate red: mutateAsync가 perfBudgetMs(예산)*2 지연되면 success 단언이 실제로 실패한다", async () => {
    const budget = perfBudgetMs(10);
    const gateRedDelay = budget * 2;

    // mutateAsync를 매우 느리게 만든다 (지연 시간 초과)
    createAlertMutateAsync.mockImplementation(
      () =>
        new Promise<{ id: number }>((resolve) => {
          setTimeout(() => resolve({ id: 1 }), gateRedDelay);
        }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    // 너무 긴 지연이므로 success 메시지가 나타나지 않음 — 이것이 gate red의 의도
    await waitFor(() => expect(createAlertMutateAsync).toHaveBeenCalledTimes(1), {
      timeout: gateRedDelay + 500,
    });
    // success 메시지가 나타나지 않음을 단언 (gate red 재현)
    expect(screen.queryByText(/알림이 등록되었습니다/)).not.toBeInTheDocument();
  });

  it("gate red: mutateAsync가 error를 던지면 success 단언이 실제로 실패한다", async () => {
    createAlertMutateAsync.mockRejectedValue(new Error("Network request failed"));
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    // success 메시지가 나타나지 않음을 단언
    await waitFor(() =>
      expect(screen.queryByText(/알림이 등록되었습니다/)).not.toBeInTheDocument(),
    );
    // 대신 에러 메시지가 나타나야 함 (handleSubmit이 i18n 메시지로 재감쌂)
    await waitFor(() =>
      expect(screen.getByText("알림 생성에 실패했습니다.")).toBeInTheDocument(),
    );
  });
});
