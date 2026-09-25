import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiRouteNotImplementedError, buildApiError, type AiClient } from "@aios/api-client";
import type { AgentTokenView, AiProviderSettingView, ExperimentView, StrategyProposalView } from "@aios/shared-types";
import { AiStudioPage, type AiStudioPageProps } from "./AiStudioPage";
import { perfBudgetMs } from "../../test/perfBudget";
// i18n/index.ts(task-2685)의 initI18n()이 모듈 로드 시 1회 부수효과로 실행된다 —
// main.tsx는 앱 부팅 시 이 모듈을 먼저 import해 전역 i18next 인스턴스에 catalog.ko를
// 등록한다. 이 화면(AiStudioPage.tsx)은 문구를 전부 useTranslation()의 t(key)로
// 조회하므로(check_i18n_literals.mjs 게이트), 테스트도 같은 부수효과를 먼저
// 일으키지 않으면 t()가 키 문자열 그대로("ai.tokens.revoke")를 반환해 렌더된 한국어
// 문구를 기대하는 단언이 전부 깨진다.
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

function agentToken(overrides: Partial<AgentTokenView> = {}): AgentTokenView {
  return {
    tokenId: "token-1",
    scopes: ["read", "research"],
    allowInstruments: [],
    notionalCap: "0",
    paperOnly: true,
    expiresAt: "2026-12-31T00:00:00Z",
    revoked: false,
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function proposal(overrides: Partial<StrategyProposalView> = {}): StrategyProposalView {
  return {
    proposalId: "proposal-1",
    hypothesis: "모멘텀이 강한 구간에서 추세추종",
    dataScopeInstruments: ["BTC-USDT"],
    providerRef: "anthropic",
    createdByToken: "token-1",
    outcome: "PASS",
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function experiment(overrides: Partial<ExperimentView> = {}): ExperimentView {
  return {
    experimentId: "exp-1",
    kind: "backtest",
    reproducibilityKey: "key-1",
    metrics: { sharpe: "1.2", maxDrawdownPct: "8.5" },
    parentId: null,
    createdBy: "token-1",
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function stubClient(overrides: Partial<AiClient> = {}): AiClient {
  return {
    listProviderSettings: vi.fn().mockResolvedValue([providerSetting()]),
    updateProviderSetting: vi.fn().mockResolvedValue(providerSetting()),
    listAgentTokens: vi.fn().mockResolvedValue([agentToken()]),
    issueAgentToken: vi.fn().mockResolvedValue(agentToken()),
    revokeAgentToken: vi.fn().mockResolvedValue(undefined),
    listProposals: vi.fn().mockResolvedValue([proposal()]),
    requestPromoteTicket: vi
      .fn()
      .mockResolvedValue({ ticketId: "ticket-1", actionDigest: "digest-abc", expiresAt: "2026-09-01T00:05:00Z" }),
    confirmPromote: vi.fn().mockResolvedValue(undefined),
    listExperiments: vi.fn().mockResolvedValue([experiment(), experiment({ experimentId: "exp-2" })]),
    ...overrides,
  };
}

function renderPage(props: AiStudioPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/ai/studio"]}>
        <Routes>
          <Route path="/ai/studio" element={<AiStudioPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AiStudioPage", () => {
  it("공급자·토큰·제안·실험 데이터를 각 섹션에 렌더한다", async () => {
    renderPage({ aiClient: stubClient() });

    expect(await screen.findByText("Anthropic")).toBeInTheDocument();
    expect(await screen.findByText("token-1")).toBeInTheDocument();
    expect(await screen.findByText("proposal-1")).toBeInTheDocument();
    expect(await screen.findByTestId("ai-experiment-comparison-table")).toBeInTheDocument();
  });

  it("제안이 없으면 빈 상태를 보여준다(negative 1/3)", async () => {
    renderPage({ aiClient: stubClient({ listProposals: vi.fn().mockResolvedValue([]) }) });
    expect(await screen.findByText("제안이 없습니다.")).toBeInTheDocument();
  });

  it("라우트가 아직 없으면(AiRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다(negative 2/3)", async () => {
    const client = stubClient({
      listAgentTokens: vi.fn().mockRejectedValue(new AiRouteNotImplementedError("ai.tokens.base")),
    });
    renderPage({ aiClient: client });

    expect(await screen.findByText("AI 에이전트 토큰 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("PASS가 아닌 제안은 PAPER 승격 버튼을 보여주지 않는다(negative 3/3)", async () => {
    renderPage({
      aiClient: stubClient({
        listProposals: vi.fn().mockResolvedValue([proposal({ proposalId: "proposal-2", outcome: "FAIL" })]),
      }),
    });

    await screen.findByText("proposal-2");
    expect(screen.queryByText("PAPER 승격")).not.toBeInTheDocument();
  });

  // 실패주입: FollowPage.test.tsx(UX-15)와 동일 관용 — ApiError가 아닌 진짜 네트워크
  // 실패도 크래시 없이 오류 배너로 보여주고, buildApiError(실제 HTTP 파싱 경로)로
  // 만든 429는 재시도 가능 갈래를 실제 분류 파이프라인(routeApiError)으로 태운다.
  it("실패주입 1/2: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 오류 배너로 보여준다", async () => {
    renderPage({
      aiClient: stubClient({ listAgentTokens: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) }),
    });

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 2/2: buildApiError로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-ai-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const listProposals = vi.fn().mockRejectedValue(realError);
    renderPage({ aiClient: stubClient({ listProposals }) });

    expect(await screen.findByText("지원코드: trace-ai-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(listProposals).toHaveBeenCalledTimes(2));
  });

  // 게이트적색: 아래 두 테스트는 AiStudioPage.tsx의 승격 확인 흐름(PromoteConfirmPanel —
  // PAPER 승격 클릭 시 티켓 요청 후 digest를 보여주고, 확인 클릭 시에만
  // confirmPromote를 호출하며, 성공하면 제안·실험 목록을 재조회한다)을 정확히
  // 겨눈다 — 그 배선을 되돌리면(예: 확인 없이 바로 confirmPromote를 부르거나,
  // 성공 후 재조회를 지우면) 해당 테스트만 적색이 된다.
  it("게이트적색 1/2: PAPER 승격 클릭 -> 확인 digest 표시 -> 확인 클릭 시에만 승격을 실행하고 목록을 재조회한다", async () => {
    const requestPromoteTicket = vi
      .fn()
      .mockResolvedValue({ ticketId: "ticket-9", actionDigest: "digest-xyz", expiresAt: "2026-09-01T00:05:00Z" });
    const confirmPromote = vi.fn().mockResolvedValue(undefined);
    const listProposals = vi.fn().mockResolvedValue([proposal()]);
    const listExperiments = vi.fn().mockResolvedValue([experiment()]);
    renderPage({
      aiClient: stubClient({ requestPromoteTicket, confirmPromote, listProposals, listExperiments }),
    });

    fireEvent.click(await screen.findByText("PAPER 승격"));

    await waitFor(() => expect(requestPromoteTicket).toHaveBeenCalledWith("proposal-1"));
    expect(await screen.findByText("digest-xyz")).toBeInTheDocument();
    expect(confirmPromote).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("확인"));

    await waitFor(() => expect(confirmPromote).toHaveBeenCalledWith("proposal-1", "ticket-9"));
    await waitFor(() => expect(listProposals).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(listExperiments).toHaveBeenCalledTimes(2));
  });

  it("게이트적색 2/2: 승격 확인이 실패하면 오류 배너를 보여주고 목록은 재조회하지 않는다", async () => {
    const confirmPromote = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const listProposals = vi.fn().mockResolvedValue([proposal()]);
    renderPage({ aiClient: stubClient({ confirmPromote, listProposals }) });

    fireEvent.click(await screen.findByText("PAPER 승격"));
    await screen.findByText("digest-abc");
    fireEvent.click(screen.getByText("확인"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(listProposals).toHaveBeenCalledTimes(1);
  });

  it("토큰 폐기 버튼을 누르면 revokeAgentToken을 호출하고 목록을 다시 불러온다", async () => {
    const listAgentTokens = vi
      .fn()
      .mockResolvedValueOnce([agentToken({ tokenId: "token-7" })])
      .mockResolvedValueOnce([agentToken({ tokenId: "token-7", revoked: true })]);
    const revokeAgentToken = vi.fn().mockResolvedValue(undefined);
    renderPage({ aiClient: stubClient({ listAgentTokens, revokeAgentToken }) });

    fireEvent.click(await screen.findByText("폐기"));

    await waitFor(() => expect(revokeAgentToken).toHaveBeenCalledWith("token-7"));
    await waitFor(() => expect(listAgentTokens).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("REVOKED")).toBeInTheDocument());
  });

  it("실험 비교 드롭다운으로 선택한 두 실험의 지표를 나란히 보여준다", async () => {
    const listExperiments = vi.fn().mockResolvedValue([
      experiment({ experimentId: "exp-a", metrics: { sharpe: "1.0" } }),
      experiment({ experimentId: "exp-b", metrics: { sharpe: "1.5" } }),
    ]);
    renderPage({ aiClient: stubClient({ listExperiments }) });

    const table = await screen.findByTestId("ai-experiment-comparison-table");
    expect(table).toHaveTextContent("exp-a");
    expect(table).toHaveTextContent("exp-b");
    expect(table).toHaveTextContent("1.0");
    expect(table).toHaveTextContent("1.5");
  });

  // 성능: 200건의 제안 목록도 예산 시간 안에 렌더해야 한다(FollowPage.test.tsx
  // task-2699 관용과 동일하게 수치 예산으로 못박는다).
  it("성능: 200건의 제안 목록도 예산 시간 안에 렌더한다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => proposal({ proposalId: `proposal-${i + 1}` }));
    renderPage({ aiClient: stubClient({ listProposals: vi.fn().mockResolvedValue(items) }) });

    const startedAt = performance_now();
    await screen.findByText("proposal-200");
    const elapsedMs = performance_now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="ai-proposal-"]')).toHaveLength(200);
  });
});

function performance_now(): number {
  return globalThis.performance.now();
}
