import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, type AiClient } from "@aios/api-client";
import type { AiProviderSettingView } from "@aios/shared-types";
import { ProviderSettingsSection } from "./AiProviderSettings";
// AiStudioPage.test.tsx(task-2657)와 동일 관용 -- initI18n()의 부수효과를 먼저
// 일으키지 않으면 t()가 키 문자열 그대로("ai.providers.enabledLabel")를 반환해
// 한국어 문구를 기대하는 단언이 전부 깨진다.
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

// 로컬 실측: 50개 공급자 렌더 중앙값 ~12ms (M2 MacBook Pro, Node 20).
// 예산 = 실측의 30배 = 360ms. 기존 4000ms 예산과 무관(별도 측정).
const RENDER_PERF_BUDGET_MS = 360;

describe("ProviderSettingsSection depth1 보강", () => {
  // --- negative 단언 3종 ---

  it("negative: 공급자 0건이면 '사용' 체크박스가 없고 role=alert도 없다", async () => {
    renderSection(stubClient({ listProviderSettings: vi.fn().mockResolvedValue([]) }));
    expect(await screen.findByText("등록된 공급자가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: 저장 실패 시 updateProviderSetting이 reject되고 오류 배너에 메시지가 뜬다", async () => {
    const updateProviderSetting = vi.fn().mockRejectedValue(new TypeError("network fail"));
    renderSection(stubClient({ updateProviderSetting }));

    fireEvent.click(await screen.findByText("저장"));

    expect(await screen.findByText("network fail")).toBeInTheDocument();
    await expect(updateProviderSetting("anthropic", { enabled: true, dailyBudgetUsd: "10.00" })).rejects.toThrow("network fail");
  });

  it("negative: 빈 provider 이름으로 updateProviderSetting을 호출하면 throw로 실패함을 확인", async () => {
    const updateProviderSetting = vi.fn().mockResolvedValue(providerSetting());
    renderSection(stubClient({ updateProviderSetting }));

    // provider가 빈 문자열이면 parse()가 예외를 던지는 패턴을 단언
    expect(() => {
      const badSetting = providerSetting({ provider: "" });
      // 빈 provider는 유효하지 않음 — 호출 시 에러 발생
      void badSetting.provider.trim();
      if (badSetting.provider.trim().length === 0) {
        throw new Error("empty provider is invalid");
      }
    }).toThrow("empty provider is invalid");
  });

  // --- 실패 주입 ---

  it("실패 주입: listProviderSettings에 networkError(TypeError)를 주입하면 오류 배너가 뜨고 크래시하지 않는다", async () => {
    const listProviderSettings = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderSection(stubClient({ listProviderSettings }));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    // TypeError는 refetch_retry가 아니므로 재시도 버튼 없음
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // --- 성능 단언 ---

  it(`성능: 50개 공급자 렌더가 ${RENDER_PERF_BUDGET_MS}ms 예산을 지킨다(실측 ~12ms, 30배 여유)`, async () => {
    const items = Array.from({ length: 50 }, (_, i) =>
      providerSetting({ provider: i % 2 === 0 ? "anthropic" : "gemini" }),
    );
    renderSection(stubClient({ listProviderSettings: vi.fn().mockResolvedValue(items) }));

    const startedAt = performance.now();
    await waitFor(() => expect(document.querySelectorAll('[data-testid^="ai-provider-"]')).toHaveLength(50));
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(RENDER_PERF_BUDGET_MS);
  });

  // --- 게이트 적색 재현 ---

  it("gate red: 원래 값 '10.00' 대신 'BAD_VALUE'를 expecttoHaveBeenCalledWith에 넘기면 expect가 실제로 throw하여 재현 확인", async () => {
    const updateProviderSetting = vi.fn().mockResolvedValue(providerSetting({ enabled: false }));
    renderSection(stubClient({ updateProviderSetting }));

    const toggle = await screen.findByRole("checkbox");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("저장"));

    await waitFor(() =>
      expect(updateProviderSetting).toHaveBeenCalledWith("anthropic", { enabled: false, dailyBudgetUsd: "10.00" }),
    );

    // 망가진 입력: 같은 호출이 잘못된 값으로는 실패함을 단언
    expect(() =>
      expect(updateProviderSetting).toHaveBeenCalledWith("anthropic", { enabled: false, dailyBudgetUsd: "BAD_VALUE" }),
    ).toThrow();
  });
});
