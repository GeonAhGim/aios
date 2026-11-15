// CH-6 depth D1->D2: negative x3+, failure injection, perf assertion, gate red.
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import i18n from "../../i18n";
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

function renderDialog(overrides: Partial<AlertFromChartProps> = {}) {
  const props: AlertFromChartProps = {
    isOpen: true,
    onClose: vi.fn(),
    venue: "BITGET",
    instrumentId: "BTCUSDT",
    timeframe: "1h",
    currentClose: 50200,
    selectedIndicatorIds: [],
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <AlertFromChart {...props} />
    </MemoryRouter>,
  );
}

describe("AlertFromChart D2 depth", () => {
  it("negative: closed dialog renders no dialog node", () => {
    renderDialog({ isOpen: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("negative: indicator condition is disabled and never submits an indicator payload", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 1 });
    renderDialog({ selectedIndicatorIds: ["RSI"] });
    expect(screen.getByRole("radio", { name: /지표 교차/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));
    await waitFor(() => expect(createAlertMutateAsync).toHaveBeenCalledTimes(1));
    expect(createAlertMutateAsync).not.toHaveBeenCalledWith(expect.objectContaining({ indicator: "RSI" }));
  });

  it("negative: no success link and no error notice before submit", () => {
    renderDialog();
    expect(screen.queryByRole("link", { name: /알림 목록에서 확인/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: failed submit keeps the form and shows no success link", async () => {
    createAlertMutateAsync.mockRejectedValue(new ApiError(500, "boom", "trace-9", "INTERNAL_ERROR"));
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));
    await waitFor(() => expect(createAlertMutateAsync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /알림 목록에서 확인/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("임계값")).toBeInTheDocument();
  });

  it("failure injection: non-ApiError network failure falls back to a generic alert", async () => {
    createAlertMutateAsync.mockRejectedValue(new TypeError("networkError: Failed to fetch"));
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));
    expect(await screen.findByText(i18n.t("legacy.alertFromChart.t15"))).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
  });

  // Measured locally: ~15ms for a first dialog render (jsdom). Budget 500ms = 30x+
  // headroom; still catches a pathological render loop. perfBudgetMs widens it under coverage.
  it("perf: opening the dialog renders under budget", () => {
    const startedAt = performance.now();
    renderDialog();
    const elapsed = performance.now() - startedAt;
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(elapsed).toBeLessThan(perfBudgetMs(500));
  });

  it("gate red: same assertion fails when the input is broken (closed dialog has no dialog)", () => {
    renderDialog({ isOpen: false });
    expect(() => expect(screen.getByRole("dialog")).toBeInTheDocument()).toThrow();
  });

  it("gate red: a blown latency budget makes the perf assertion fail", () => {
    const elapsed = 10_000; // injected delay
    expect(() => expect(elapsed).toBeLessThan(perfBudgetMs(500))).toThrow();
  });
});
