import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, SignalsRouteNotImplementedError, type SignalsClient } from "@aios/api-client";
import type {
  SignalReceiptListResponse,
  SignalSourceListResponse,
  SignalSourceResponse,
  SignalSourceSecretIssueResponse,
} from "@aios/shared-types";
import { SignalSourcesPage, type SignalSourcesPageProps } from "./SignalSourcesPage";
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

function issueResponse(overrides: Partial<SignalSourceSecretIssueResponse> = {}): SignalSourceSecretIssueResponse {
  return {
    source: source(),
    plaintextSecret: "sk_live_full_plaintext_secret_value",
    ...overrides,
  };
}

function stubClient(overrides: Partial<SignalsClient> = {}): SignalsClient {
  return {
    listSources: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalSourceListResponse),
    issueSource: vi.fn().mockResolvedValue(issueResponse()),
    rotateSecret: vi.fn().mockResolvedValue(issueResponse()),
    disableSource: vi.fn().mockResolvedValue(undefined),
    listReceipts: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalReceiptListResponse),
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

describe("SignalSourcesPage", () => {
  it("소스가 없으면 빈 상태를 보여준다(negative 1/4)", async () => {
    renderPage({ signalsClient: stubClient() });
    expect(await screen.findByTestId("signal-sources-empty")).toBeInTheDocument();
  });

  it("소스의 수신 로그가 없으면 명시적 빈 상태를 보여준다(negative 2/4, DoD (2))", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("최근 수신 로그"));
    expect(await screen.findByTestId("signal-receipts-empty")).toBeInTheDocument();
    expect(client.listReceipts).toHaveBeenCalledWith(1);
  });

  it("발급 직후 시크릿 원문을 1회 보여준다", async () => {
    const client = stubClient();
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "새 소스" } });
    fireEvent.click(screen.getByText("발급"));

    expect(await screen.findByTestId("signal-secret-plaintext")).toHaveTextContent(
      "sk_live_full_plaintext_secret_value",
    );
    expect(client.issueSource).toHaveBeenCalledWith({ name: "새 소스" });
  });

  it("발급 후 목록을 재조회해도 원문 시크릿은 다시 나타나지 않는다(negative 3/4, DoD (1) 1회성 노출)", async () => {
    const listSources = vi
      .fn()
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [source()], total: 1 });
    const client = stubClient({ listSources });
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "새 소스" } });
    fireEvent.click(screen.getByText("발급"));
    await screen.findByTestId("signal-secret-plaintext");

    fireEvent.click(screen.getByText("확인"));

    await waitFor(() => expect(listSources).toHaveBeenCalledTimes(2));
    await screen.findByText("sk_live_****ab12");
    expect(screen.queryByTestId("signal-secret-plaintext")).not.toBeInTheDocument();
    expect(screen.queryByText("sk_live_full_plaintext_secret_value")).not.toBeInTheDocument();
  });

  it("라우트가 아직 없으면(SignalsRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다(negative 4/4)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new SignalsRouteNotImplementedError("signals.sources.base")),
    });
    renderPage({ signalsClient: client });

    expect(await screen.findByText("신호 소스 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    });
    renderPage({ signalsClient: client });

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("buildApiError로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-signals-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const listSources = vi.fn().mockRejectedValue(realError);
    renderPage({ signalsClient: stubClient({ listSources }) });

    expect(await screen.findByText("지원코드: trace-signals-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(listSources).toHaveBeenCalledTimes(2));
  });

  // 게이트적색: SignalSourcesPage.tsx의 handleRotate 분기(회전 성공 시 새 원문을
  // revealed에 실어 노출)를 정확히 겨눈다 — rotateSecret 호출 자체를 지우거나
  // setRevealed를 빼먹으면 이 테스트만 적색이 된다.
  it("게이트적색: 회전 버튼을 누르면 rotateSecret을 호출하고 새 원문을 1회 노출한다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockResolvedValue(
        issueResponse({ plaintextSecret: "sk_live_rotated_plaintext_value" }),
      ),
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("회전"));

    expect(await screen.findByTestId("signal-secret-plaintext")).toHaveTextContent(
      "sk_live_rotated_plaintext_value",
    );
    expect(client.rotateSecret).toHaveBeenCalledWith(1);
  });

  it("성능: 200건의 소스 목록도 예산 시간 안에 렌더한다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => source({ id: i + 1, name: `소스 ${i + 1}` }));
    renderPage({
      signalsClient: stubClient({ listSources: vi.fn().mockResolvedValue({ items, total: items.length }) }),
    });

    const startedAt = performance_now();
    await screen.findByText("소스 200");
    const elapsedMs = performance_now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="signal-source-"]')).toHaveLength(200);
  });
});

function performance_now(): number {
  return globalThis.performance.now();
}
