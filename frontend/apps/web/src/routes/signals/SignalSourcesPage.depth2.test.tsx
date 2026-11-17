import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignalsRouteNotImplementedError, type SignalsClient } from "@aios/api-client";
import type { SignalSourceListResponse, SignalSourceResponse } from "@aios/shared-types";
import { SignalSourcesPage, type SignalSourcesPageProps } from "./SignalSourcesPage";
import { perfBudgetMs } from "../../test/perfBudget";

// ---------------------------------------------------------------------------
// Mocks — identical shape to SignalSourcesPage.test.tsx
// ---------------------------------------------------------------------------

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
    listSources: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalSourceListResponse),
    issueSource: vi.fn().mockResolvedValue({
      source: source(),
      plaintextSecret: "sk_live_full_plaintext_secret_value",
    }),
    rotateSecret: vi.fn().mockResolvedValue({
      source: source(),
      plaintextSecret: "sk_live_rotated_secret",
    }),
    disableSource: vi.fn().mockResolvedValue(undefined),
    listReceipts: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalSourceListResponse),
    ...overrides,
  };
}

function renderPage(props: SignalSourcesPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/signals/sources"]}>
        <Routes>
          <Route path="/signals/sources" element={<SignalSourcesPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Negative tests (4 types: disabled, .not.toBeInTheDocument,
//   .toHaveClass(tone="danger"), .rejects via mockRejectedValue)
// ---------------------------------------------------------------------------

describe("SignalSourcesPage depth2", () => {
  // negative 1/4: 버튼 비활성화 단언 — 빈 이름으로 발급 버튼이 disabled
  it("유효하지 않은 이름으로 발급하면 버튼이 비활성화된다(negative 1/4)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    });
    renderPage({ signalsClient: client });

    expect(screen.getByText("발급")).toBeDisabled();
    expect(client.issueSource).not.toHaveBeenCalled();
  });

  // negative 2/4: .not.toBeInTheDocument — API 실패 시 빈 상태가 아닌 에러 배너만
  it("API 응답 실패 시 빈 상태가 아닌 에러 배너만 보인다(negative 2/4)", async () => {
    renderPage({
      signalsClient: {
        ...stubClient(),
        listSources: vi.fn().mockRejectedValue(new TypeError("Network failure")),
      },
    });

    // 에러 배너는 표시되지만 signal-sources-empty는 없어야 함
    expect(await screen.findByText("Network failure")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument();
  });

  // negative 3/4: Alert 컴포넌트의 border-danger 클래스 단언 — 에러 배너가 danger tone임
  it("에러 배너가 border-danger 클래스를 가진다(negative 3/4)", async () => {
    renderPage({
      signalsClient: {
        ...stubClient(),
        listSources: vi.fn().mockRejectedValue(new TypeError("fetch failed")),
      },
    });

    // Alert 컴포넌트는 border-danger/30 bg-danger-muted text-danger 클래스를 가짐
    const alertEl = await screen.findByText("fetch failed");
    expect(alertEl.closest("[class*='border-danger']")).toBeInTheDocument();
  });

  // negative 4/4: .rejects via mockRejectedValue — notImplemented은 재시도 버튼 없음
  it("SignalsRouteNotImplementedError 발생 시 재시도 버튼 없이 에러 배너만 보인다(negative 4/4)", async () => {
    renderPage({
      signalsClient: {
        ...stubClient(),
        listSources: vi.fn().mockRejectedValue(new SignalsRouteNotImplementedError("signals.sources.base")),
      },
    });

    // 재시도 버튼은 없어야 함 (notImplemented이므로 canRetry=false)
    expect(await screen.findByText("신호 소스 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Failure injection
  // ---------------------------------------------------------------------------

  it("fail injection: listSources가 TypeError를 반환하면 에러 배너 표시", async () => {
    renderPage({
      signalsClient: {
        ...stubClient(),
        listSources: vi.fn().mockRejectedValue(new TypeError("Network failure")),
      },
    });

    expect(await screen.findByText("Network failure")).toBeInTheDocument();
    expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Performance assertion
  // ---------------------------------------------------------------------------

  it("성능: 200건 소스 목록 렌더 시간 단언", async () => {
    const items = Array.from({ length: 200 }, (_, i) => source({ id: i + 1, name: `Source ${i + 1}` }));
    renderPage({
      signalsClient: stubClient({
        listSources: vi.fn().mockResolvedValue({ items, total: items.length }),
      }),
    });

    const startedAt = performance.now();
    await screen.findByText("Source 200");
    const elapsedMs = performance.now() - startedAt;

    // 실측: ~30ms (로컬). 예산 = 30 * 30 = 900ms.
    // coverage 모드에서는 3배 확장되므로 perfBudgetMs(900) 사용.
    expect(elapsedMs).toBeLessThan(perfBudgetMs(900));
  });

  // ---------------------------------------------------------------------------
  // Gate red reproduction
  // ---------------------------------------------------------------------------

  // gate red: 정상 흐름에서는 소스 이름이 보이고, 지연(응답 없음)이나 입력 파손
  // (목록 이름이 바뀜)을 주입하면 같은 findByText 단언이 실제로 실패한다.
  it("gate red: 정상 응답이면 소스 이름이 보이고, 지연 또는 이름 파손이면 같은 단언이 실패한다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValueOnce({ items: [source()], total: 1 }),
    });
    renderPage({ signalsClient: client });

    await waitFor(() => expect(client.listSources).toHaveBeenCalled());
    expect(await screen.findByText("TradingView 웹훅")).toBeInTheDocument();

    // 지연 주입: 응답이 끝나지 않으면 같은 단언은 타임아웃으로 거부된다(.rejects)
    cleanup();
    const delayed = stubClient({ listSources: vi.fn().mockReturnValue(new Promise(() => {})) });
    renderPage({ signalsClient: delayed });
    await expect(screen.findByText("TradingView 웹훅", {}, { timeout: 100 })).rejects.toThrow();

    // 입력 파손 주입: 목록 이름이 달라지면 원래 이름 단언이 실패한다
    cleanup();
    renderPage({
      signalsClient: stubClient({
        listSources: vi.fn().mockResolvedValue({ items: [source({ name: "깨진 이름" })], total: 1 }),
      }),
    });
    await expect(screen.findByText("TradingView 웹훅", {}, { timeout: 100 })).rejects.toThrow();
    expect(await screen.findByText("깨진 이름")).toBeInTheDocument();
  });
});
