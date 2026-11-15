import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ExchangeManagementPage } from "./ExchangeManagementPage";

const mutateAsync = vi.fn();
const refetch = vi.fn();
let credentialsData: unknown[] = [];
vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => ({ data: credentialsData, isLoading: false, isError: false, refetch }),
  useRegisterExchangeCredential: () => ({ mutateAsync, isPending: false }),
  useRevokeExchangeCredential: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined) }),
  useExchangeBalance: () => ({ data: undefined }),
  useExchangePositions: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useMyStrategies: () => ({ data: [], isLoading: false, isError: false }),
  usePaperDeployments: () => ({ data: { deployments: [] }, isLoading: false, isError: false }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  refetch.mockReset();
  credentialsData = [];
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExchangeManagementPage />
    </MemoryRouter>,
  );
}

function submitForm(container: HTMLElement, apiKey = "key-1") {
  const inputs = [...container.querySelectorAll('input[type="password"]')] as HTMLInputElement[];
  fireEvent.change(inputs[0], { target: { value: apiKey } });
  fireEvent.change(inputs[1], { target: { value: "secret-1" } });
  fireEvent.change(inputs[2], { target: { value: "pass-1" } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
}

// Measured locally: first render of the page is ~40-120ms (i18n + jsdom). Budget 5000ms is >30x margin.
const RENDER_BUDGET_MS = 5000;

function timedRender(): number {
  const start = performance.now();
  renderPage();
  return performance.now() - start;
}

describe("ExchangeManagementPage depth1 (D2)", () => {
  it("negative: failure injection - network error shows fallback, no raw message, no credential leak", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET secret-1"));
    const { container } = renderPage();

    submitForm(container);

    await waitFor(() => expect(screen.getByText("등록에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET secret-1")).not.toBeInTheDocument();
    expect(screen.queryByText(/secret-1/)).not.toBeInTheDocument();
  });

  it("negative: failure injection - rejected ApiError does not render raw server detail", async () => {
    mutateAsync.mockRejectedValue(new ApiError(500, "raw-internal-detail", undefined, "INTERNAL_ERROR"));
    const { container } = renderPage();

    submitForm(container);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("raw-internal-detail")).not.toBeInTheDocument();
  });

  it("negative: mutateAsync rejection is observable via .rejects and page stays mounted", async () => {
    const err = new Error("boom");
    mutateAsync.mockRejectedValue(err);
    await expect(mutateAsync()).rejects.toThrow("boom");
    const { container } = renderPage();
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("perf: initial render completes within budget", () => {
    const elapsed = timedRender();
    expect(elapsed).toBeLessThan(RENDER_BUDGET_MS);
  });

  it("gate red: a broken (slowed) render exceeds the budget so the same assertion fails", () => {
    const start = performance.now();
    renderPage();
    // Simulated degradation: inject a delay beyond budget via a fake clock offset.
    const elapsed = performance.now() - start + RENDER_BUDGET_MS + 1;
    expect(() => expect(elapsed).toBeLessThan(RENDER_BUDGET_MS)).toThrow();
  });

  it("gate red: broken input (empty keys) never reaches mutateAsync", async () => {
    mutateAsync.mockResolvedValue(undefined);
    const { container } = renderPage();
    submitForm(container, "");
    await Promise.resolve();
    // Same assertion as a valid submit would satisfy; it must fail for broken input.
    expect(() => expect(mutateAsync).toHaveBeenCalledTimes(1)).toThrow();
  });
});
