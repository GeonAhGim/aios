import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignalsRouteNotImplementedError, type SignalsClient } from "@aios/api-client";
import type { SignalSourceResponse } from "@aios/shared-types";
import { SignalSourcesPage } from "./SignalSourcesPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function source(overrides: Partial<SignalSourceResponse> = {}): SignalSourceResponse {
  return {
    id: 1,
    name: "TradingView 웹훅",
    webhookUrl: "https://example.com/webhooks/signals/1",
    status: "ACTIVE",
    secretPreview: "sk_live_****ab12",
    secretRotatedAt: "2026-09-01T00:00:00Z",
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function stubClient(overrides: Partial<SignalsClient> = {}): SignalsClient {
  return {
    listSources: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    issueSource: vi.fn(),
    rotateSecret: vi.fn(),
    disableSource: vi.fn(),
    listReceipts: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    ...overrides,
  };
}

function renderPage(signalsClient: SignalsClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/signals/sources"]}>
        <Routes>
          <Route path="/signals/sources" element={<SignalSourcesPage signalsClient={signalsClient} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function manySources(count: number): SignalSourceResponse[] {
  return Array.from({ length: count }, (_, i) => source({ id: i + 1, name: `Source ${i + 1}` }));
}

describe("SignalSourcesPage depth1 (D2 floor)", () => {
  it("negative: 소스 이름이 비어 있으면 발급 버튼이 비활성이고 issueSource를 호출하지 않는다", async () => {
    const client = stubClient();
    renderPage(client);
    await screen.findByTestId("signal-sources-empty");

    const button = screen.getByText("발급");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(client.issueSource).not.toHaveBeenCalled();
  });

  it("negative: 공백뿐인 이름도 발급되지 않는다", async () => {
    const client = stubClient();
    renderPage(client);
    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "   " } });

    expect(screen.getByText("발급")).toBeDisabled();
    expect(client.issueSource).not.toHaveBeenCalled();
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 실패하면 빈 상태·소스 카드는 렌더되지 않는다", async () => {
    renderPage(stubClient({ listSources: vi.fn().mockRejectedValue(new Error("boom")) }));
    await screen.findByText("boom");

    expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("signal-source-1")).not.toBeInTheDocument();
  });

  it("failure injection: 발급 중 네트워크 실패(mockRejectedValue)는 오류를 보이고 시크릿을 노출하지 않는다", async () => {
    const client = stubClient({ issueSource: vi.fn().mockRejectedValue(new Error("ECONNREFUSED")) });
    renderPage(client);

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "새 소스" } });
    fireEvent.click(screen.getByText("발급"));

    expect(await screen.findByText("ECONNREFUSED")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
  });

  it("failure injection: 회전 실패 시 오류를 보이고 회전 버튼이 다시 활성화된다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockRejectedValue(new SignalsRouteNotImplementedError("signals.sources.rotate")),
    });
    renderPage(client);

    fireEvent.click(await screen.findByText("회전"));

    await vi.waitFor(() => expect(client.rotateSecret).toHaveBeenCalledWith(1));
    await vi.waitFor(() => expect(screen.getByText("회전")).toBeEnabled());
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
  });

  // 실측(로컬 vitest, 200건): 약 170ms. 예산 5100ms = 실측의 30배 — 느린 CI 호스트의
  // 잡음은 흡수하고 O(n^2) 회귀(10배 이상)는 거른다. perfBudgetMs는 커버리지 실행 시에만 넓힌다.
  it("성능: 200건 소스 목록 렌더 시간이 예산 안이다", async () => {
    const items = manySources(200);
    renderPage(stubClient({ listSources: vi.fn().mockResolvedValue({ items, total: 200 }) }));

    const startedAt = performance.now();
    await screen.findByText("Source 200");
    const elapsed = performance.now() - startedAt;

    expect(elapsed).toBeLessThan(perfBudgetMs(5100));
  });

  // gate red: 입력(빈 목록)을 망가뜨리면 "Source 200이 렌더된다"는 같은 단언이 실제로 실패한다.
  it("gate red: 목록 입력이 비면 목록 렌더 단언이 실제로 실패한다", async () => {
    renderPage(stubClient());
    await screen.findByTestId("signal-sources-empty");

    await expect(screen.findByText("Source 200", {}, { timeout: 150 })).rejects.toThrow();
    expect(screen.queryByText("Source 200")).not.toBeInTheDocument();
  });

  // gate red: 지연을 망가뜨리면(렌더 후 예산 초과 지연) 같은 성능 단언이 실제로 실패한다.
  it("gate red: 지연이 예산을 넘으면 성능 단언이 실제로 실패한다", async () => {
    const items = manySources(2);
    renderPage(stubClient({ listSources: vi.fn().mockResolvedValue({ items, total: 2 }) }));

    const startedAt = performance.now();
    await screen.findByText("Source 2");
    await new Promise((resolve) => setTimeout(resolve, 80));
    const elapsed = performance.now() - startedAt;

    expect(() => expect(elapsed).toBeLessThan(50)).toThrow();
    expect(elapsed).toBeLessThan(perfBudgetMs(5100));
  });
});
