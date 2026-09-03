import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { WalletPage } from "./WalletPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useWalletBalance: () => ({ data: { balance: "1000" }, isLoading: false }),
  useRequestTopup: () => ({ mutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderPage() {
  render(
    <MemoryRouter>
      <WalletPage />
    </MemoryRouter>,
  );
}

function submitTopup() {
  fireEvent.click(screen.getByRole("button", { name: "충전 신청" }));
}

// task-322 후속 결함: errorCode가 ErrorMessage에 전달되지 않아 EXCHANGE_FATAL 등
// 구체적인 에러 코드도 항상 DEFAULT fallback 문구로만 보였다.
describe("WalletPage 에러 표시", () => {
  it("ApiError.errorCode가 있으면 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(409, "raw server detail", undefined, "STATE_CONCURRENCY_CONFLICT"),
    );
    renderPage();

    submitTopup();

    await waitFor(() =>
      expect(
        screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitTopup();

    await waitFor(() => expect(screen.getByText("충전 요청에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("traceId가 있으면 지원코드를 함께 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(500, "internal", "trace-wallet-1", "INTERNAL_ERROR"),
    );
    renderPage();

    submitTopup();

    await waitFor(() => expect(screen.getByText("지원코드: trace-wallet-1")).toBeInTheDocument());
  });

  // task-930 §9 PLT-25: 429 RATE_LIMIT_EXCEEDED는 routeApiError의 backoff_retry로
  // 판정돼 재시도 버튼이 카운트다운 후 활성화된다.
  it("negative: 429 RATE_LIMIT_EXCEEDED는 retry_after 카운트다운 후 재시도 버튼을 활성화한다", async () => {
    vi.useFakeTimers();
    try {
      mutateAsync.mockRejectedValue(
        new ApiError(429, "too many requests", "trace-wallet-2", "RATE_LIMIT_EXCEEDED", 2),
      );
      renderPage();

      await act(async () => submitTopup());

      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument();
      const retryButton = screen.getByRole("button", { name: "다시 시도" });
      expect(retryButton).toBeDisabled();
      expect(screen.getByText("2초 후 재시도 가능")).toBeInTheDocument();

      await act(async () => vi.advanceTimersByTime(1000));
      await act(async () => vi.advanceTimersByTime(1000));
      expect(retryButton).toBeEnabled();

      vi.useRealTimers();
      mutateAsync.mockResolvedValueOnce({ id: 7, requestedAmount: "30000" });
      retryButton.click();
      await waitFor(() => expect(screen.getByText(/충전 요청 #7/)).toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
  });
});
