import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiRouteNotImplementedError, buildApiError, type AiClient } from "@aios/api-client";
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

describe("ProviderSettingsSection", () => {
  it("공급자 이름·사용 토글·일일 비용 상한 필드를 렌더한다", async () => {
    renderSection(stubClient());

    expect(await screen.findByText("Anthropic")).toBeInTheDocument();
    const toggle = screen.getByRole("checkbox");
    expect(toggle).toBeChecked();
    expect(screen.getByDisplayValue("10.00")).toBeInTheDocument();
    expect(screen.getByText("사용")).toBeInTheDocument();
    expect(screen.getByText("일일 비용 상한(USD)")).toBeInTheDocument();
  });

  it("공급자가 없으면 빈 상태를 보여준다(negative 1/3)", async () => {
    renderSection(stubClient({ listProviderSettings: vi.fn().mockResolvedValue([]) }));
    expect(await screen.findByText("등록된 공급자가 없습니다.")).toBeInTheDocument();
  });

  it("유령 경로(AiRouteNotImplementedError)면 fetch 호출 없이 오류 배너만 보여주고 재시도 버튼은 없다(negative 2/3)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const listProviderSettings = vi.fn().mockRejectedValue(new AiRouteNotImplementedError("ai.providers.base"));
    renderSection(stubClient({ listProviderSettings }));

    expect(await screen.findByText("AI 공급자 설정 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("저장 요청이 진짜 네트워크 오류로 실패해도 크래시 없이 오류 배너로 보여준다(negative 3/3)", async () => {
    const updateProviderSetting = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderSection(stubClient({ updateProviderSetting }));

    fireEvent.click(await screen.findByText("저장"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
  });

  it("실패주입: buildApiError로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-ai-providers-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const listProviderSettings = vi.fn().mockRejectedValue(realError);
    renderSection(stubClient({ listProviderSettings }));

    expect(await screen.findByText("지원코드: trace-ai-providers-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(listProviderSettings).toHaveBeenCalledTimes(2));
  });

  it("사용 토글을 끄고 저장하면 updateProviderSetting에 enabled: false로 전달한다(게이트적색)", async () => {
    const updateProviderSetting = vi.fn().mockResolvedValue(providerSetting({ enabled: false }));
    renderSection(stubClient({ updateProviderSetting }));

    const toggle = await screen.findByRole("checkbox");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("저장"));

    await waitFor(() =>
      expect(updateProviderSetting).toHaveBeenCalledWith("anthropic", { enabled: false, dailyBudgetUsd: "10.00" }),
    );
  });

  it("성능: 50개 공급자 목록도 예산 시간 안에 렌더한다", async () => {
    const items = Array.from({ length: 50 }, (_, i) =>
      providerSetting({ provider: i % 2 === 0 ? "anthropic" : "gemini" }),
    );
    renderSection(stubClient({ listProviderSettings: vi.fn().mockResolvedValue(items) }));

    const startedAt = performance.now();
    await waitFor(() => expect(document.querySelectorAll('[data-testid^="ai-provider-"]')).toHaveLength(50));
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(4000);
  });
});
