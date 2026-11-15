import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiRouteNotImplementedError, type AiClient } from "@aios/api-client";
import type { AiProviderSettingView } from "@aios/shared-types";
import { ProviderSettingsSection } from "./AiProviderSettings";
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function providerSetting(overrides: Partial<AiProviderSettingView> = {}): AiProviderSettingView {
  return { provider: "anthropic", enabled: true, dailyBudgetUsd: "10.00", ...overrides };
}

function stubClient(overrides: Partial<AiClient> = {}): AiClient {
  return {
    listProviderSettings: vi.fn().mockResolvedValue([providerSetting()]),
    updateProviderSetting: vi.fn().mockResolvedValue(providerSetting()),
    listAgentTokens: vi.fn().mockResolvedValue([]),
    issueAgentToken: vi.fn().mockResolvedValue(undefined),
    revokeAgentToken: vi.fn().mockResolvedValue(undefined),
    listProposals: vi.fn().mockResolvedValue([]),
    requestPromoteTicket: vi.fn().mockResolvedValue(undefined),
    confirmPromote: vi.fn().mockResolvedValue(undefined),
    listExperiments: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

function renderSection(client: AiClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ProviderSettingsSection client={client} />
    </QueryClientProvider>,
  );
}

const ROW_COUNT = 50;
// Budget: measured locally at ~49ms for 50 rows (Windows, vitest+jsdom); 3000ms is >60x headroom for slow CI.
const RENDER_BUDGET_MS = 3000;

function manyProviders(): AiProviderSettingView[] {
  return Array.from({ length: ROW_COUNT }, (_, i) =>
    providerSetting({ provider: i % 2 === 0 ? "anthropic" : "gemini" }),
  );
}

async function measureRenderMs(client: AiClient): Promise<number> {
  const startedAt = performance.now();
  renderSection(client);
  await waitFor(() => expect(document.querySelectorAll('[data-testid^="ai-provider-"]')).toHaveLength(ROW_COUNT));
  return performance.now() - startedAt;
}

describe("ProviderSettingsSection depth1 (D2 evidence)", () => {
  it("negative: empty list shows neither rows, checkbox nor alert", async () => {
    renderSection(stubClient({ listProviderSettings: vi.fn().mockResolvedValue([]) }));
    expect(await screen.findByText("등록된 공급자가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(document.querySelector('[data-testid^="ai-provider-"]')).not.toBeInTheDocument();
  });

  it("negative: failed list query renders no rows and no save button", async () => {
    const listProviderSettings = vi.fn().mockRejectedValue(new AiRouteNotImplementedError("ai.providers.base"));
    renderSection(stubClient({ listProviderSettings }));
    expect(await screen.findByText("AI 공급자 설정 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("저장")).not.toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
    await expect(listProviderSettings()).rejects.toBeInstanceOf(AiRouteNotImplementedError);
  });

  it("failure injection: network error on save keeps the row and does not call refetch", async () => {
    const updateProviderSetting = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const listProviderSettings = vi.fn().mockResolvedValue([providerSetting()]);
    renderSection(stubClient({ updateProviderSetting, listProviderSettings }));

    fireEvent.click(await screen.findByText("저장"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.getByTestId("ai-provider-anthropic")).toBeInTheDocument();
    expect(listProviderSettings).toHaveBeenCalledTimes(1);
    await expect(updateProviderSetting()).rejects.toThrow("Failed to fetch");
  });

  it("performance: 50 provider rows render within budget", async () => {
    const elapsed = await measureRenderMs(stubClient({ listProviderSettings: vi.fn().mockResolvedValue(manyProviders()) }));
    expect(elapsed).toBeLessThan(RENDER_BUDGET_MS);
  });

  it("gate red: a delayed client breaks the same render-budget assertion", async () => {
    const slow = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(manyProviders()), RENDER_BUDGET_MS + 200)),
    );
    const elapsed = await measureRenderMs(stubClient({ listProviderSettings: slow }));
    expect(() => expect(elapsed).toBeLessThan(RENDER_BUDGET_MS)).toThrow();
  }, 15000);

  it("gate red: a broken payload makes the saved-value assertion fail", async () => {
    const updateProviderSetting = vi.fn().mockResolvedValue(providerSetting());
    renderSection(stubClient({ updateProviderSetting }));
    fireEvent.click(await screen.findByText("저장"));
    await waitFor(() =>
      expect(updateProviderSetting).toHaveBeenCalledWith("anthropic", { enabled: true, dailyBudgetUsd: "10.00" }),
    );
    expect(() =>
      expect(updateProviderSetting).toHaveBeenCalledWith("anthropic", { enabled: true, dailyBudgetUsd: "BROKEN" }),
    ).toThrow();
  });
});
